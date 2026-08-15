#!/usr/bin/env python3
"""
mask.py — 前處理遮罩：坡度、雷達陰影／疊掩（layover/shadow）

雷達陰影是山區水體萃取最大的坑：陡坡的低回波會被誤判為水體，
必須以 DEM 計算局部入射角，預先把「幾何上不可能量到真實回波」
的像元排除，而不是等水體萃取完再事後過濾。

本檔只處理「陸上地形幾何」造成的遮罩，不含輻射校正（見 sar.py）。
座標系一律假設輸入 DEM 已在等距投影（如 EPSG:3826），像元為正方形，
單位公尺，這樣坡度／坡向可以直接用像元差分算，不必另外做地圖投影校正。

演算法
------
1. slope_mask       — 坡度遮罩：純地形，坡度 > 門檻視為不可能的水體
2. layover_shadow_mask — 疊掩／陰影遮罩：比較地形局部入射角與
   衛星入射角、地形是否面向或背向衛星飛行方向
3. build_mask       — 兩者聯集，供 detect.water 在萃取前套用

沒有真的 SAR 場景可測時，可用 --demo 產生一顆合成山峰驗證幾何關係。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np


# ══════════════════════════════════════════
# 地形量測：坡度／坡向
# ══════════════════════════════════════════

def slope_aspect(dem: np.ndarray, cellsize: float) -> tuple[np.ndarray, np.ndarray]:
    """
    以 Horn (1981) 3x3 視窗法算坡度（度）與坡向（度，正北為 0，順時針）。

    >>> dem = np.zeros((5, 5)); dem[:, 3:] = 10.0   # 東側抬升的斜坡
    >>> slope, aspect = slope_aspect(dem, cellsize=10.0)
    >>> bool(slope[2, 2] > 0)
    True
    """
    gy, gx = np.gradient(dem, cellsize)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    aspect = (np.degrees(np.arctan2(gx, -gy))) % 360.0
    return slope, aspect


def slope_mask(dem: np.ndarray, cellsize: float, max_slope_deg: float = 45.0) -> np.ndarray:
    """
    坡度遮罩：True = 應遮蔽（坡度過陡，不太可能是穩定水面）。

    45° 為預設保守門檻；山區堰塞湖水面本身平坦，
    真正需要濾掉的是周邊陡坡誤判，門檻抓寬一點比較安全。
    """
    slope, _ = slope_aspect(dem, cellsize)
    return slope > max_slope_deg


# ══════════════════════════════════════════
# 疊掩 / 陰影（layover / shadow）
# ══════════════════════════════════════════

@dataclass
class SarGeometry:
    """單景 SAR 的觀測幾何。方位角以正北為 0，順時針，度。"""
    incidence_deg: float          # 場景中心入射角（IW: 約 29–46°）
    heading_deg: float            # 衛星飛行方向方位角
    look_side: str = "right"      # Sentinel-1 皆為右視


def local_incidence_angle(slope_deg: np.ndarray, aspect_deg: np.ndarray,
                          geom: SarGeometry) -> np.ndarray:
    """
    局部入射角（度）＝ 地形坡面法線與雷達視線方向的夾角。

    right-looking 衛星的視線方位角 = heading + 90°（順軌右側）。
    面向雷達（坡向接近視線反方向）局部入射角變小 → 疊掩風險；
    背向雷達（坡向接近視線方向）局部入射角變大 → 陰影風險。

    簡化為 2D（不含地表法線的完整 3D 向量運算），對規劃篩選已足夠，
    精確幾何校正仍須交給 SNAP 的 Terrain Correction。
    """
    look_azimuth = (geom.heading_deg + (90.0 if geom.look_side == "right" else -90.0)) % 360.0
    # 坡向與視線方向的夾角：0 表示坡面正對雷達
    facing = np.abs(((aspect_deg - look_azimuth + 180.0) % 360.0) - 180.0)
    # facing=0（正對雷達）時，坡度直接從入射角扣掉；facing=180（背對）時，加回去
    delta = slope_deg * np.cos(np.radians(facing))
    return geom.incidence_deg - delta


def layover_shadow_mask(dem: np.ndarray, cellsize: float, geom: SarGeometry,
                        layover_max_deg: float = 5.0,
                        shadow_min_deg: float = 85.0) -> np.ndarray:
    """
    疊掩／陰影遮罩：True = 應遮蔽。

    local_incidence <= layover_max_deg  → 疊掩（坡面幾乎正對雷達，
        多個地形高度的回波疊在同一個 slant-range bin）
    local_incidence >= shadow_min_deg   → 陰影（坡面幾乎完全背對雷達，
        完全收不到回波，呈現極低回波，極易與水體混淆）

    >>> dem = np.zeros((5, 5)); dem[:, 3:] = 100.0
    >>> geom = SarGeometry(incidence_deg=35.0, heading_deg=350.0)  # 近乎北向飛行，右視=東視
    >>> mask = layover_shadow_mask(dem, 10.0, geom)
    >>> mask.dtype
    dtype('bool')
    """
    slope, aspect = slope_aspect(dem, cellsize)
    lia = local_incidence_angle(slope, aspect, geom)
    return (lia <= layover_max_deg) | (lia >= shadow_min_deg)


def build_mask(dem: np.ndarray, cellsize: float, geom: SarGeometry,
              max_slope_deg: float = 45.0,
              layover_max_deg: float = 5.0,
              shadow_min_deg: float = 85.0) -> np.ndarray:
    """聯集：坡度過陡 或 疊掩 或 陰影 → True = 該像元不可用於水體萃取。"""
    return (
        slope_mask(dem, cellsize, max_slope_deg)
        | layover_shadow_mask(dem, cellsize, geom, layover_max_deg, shadow_min_deg)
    )


# ══════════════════════════════════════════
# 檔案 I/O（延遲載入 rasterio，讓核心演算法不強制依賴它）
# ══════════════════════════════════════════

def mask_from_dem_file(dem_path: str, geom: SarGeometry, **kwargs) -> tuple[np.ndarray, dict]:
    """讀 GeoTIFF DEM，回傳 (mask, profile)。profile 可原樣用來寫出遮罩 GeoTIFF。"""
    import rasterio
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float64")
        cellsize = abs(src.transform.a)
        profile = src.profile
    mask = build_mask(dem, cellsize, geom, **kwargs)
    profile.update(dtype="uint8", count=1, nodata=None)
    return mask, profile


def write_mask(mask: np.ndarray, profile: dict, out_path: str) -> None:
    import rasterio
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(mask.astype("uint8"), 1)


def cli() -> None:
    ap = argparse.ArgumentParser(description="由 DEM 產生坡度/疊掩/陰影遮罩 GeoTIFF")
    ap.add_argument("dem", help="輸入 DEM GeoTIFF")
    ap.add_argument("out", help="輸出遮罩 GeoTIFF（uint8，1=遮蔽）")
    ap.add_argument("--incidence", type=float, required=True, help="場景入射角（度）")
    ap.add_argument("--heading", type=float, required=True, help="衛星飛行方向方位角（度）")
    ap.add_argument("--max-slope", type=float, default=45.0)
    args = ap.parse_args()

    geom = SarGeometry(incidence_deg=args.incidence, heading_deg=args.heading)
    mask, profile = mask_from_dem_file(args.dem, geom, max_slope_deg=args.max_slope)
    write_mask(mask, profile, args.out)
    pct = 100.0 * mask.mean()
    print(f"✓ 已寫入 {args.out}｜遮蔽比例 {pct:.1f}%")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli()
    else:
        import doctest
        fails, total = doctest.testmod()
        print(f"mask: {total - fails}/{total} 通過")
