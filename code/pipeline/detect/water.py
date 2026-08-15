#!/usr/bin/env python3
"""
water.py — 水體萃取：SAR 低回波（Otsu 自動門檻）+ 光學 NDWI 雙軌

雙軌並行的理由
--------------
· SAR 低回波：不受雲層影響，震後／颱風後最快能拿到的資料，
  但陡坡陰影／疊掩會誤判為水體（見 preprocess.mask，須先遮罩）。
· 光學 NDWI：晴空時精度高、誤判率低，但雲層下完全拿不到——
  這正是本專案要解決的「雲層之上」問題的反面：光學可信但不可靠。

兩軌獨立各自萃取後才聯集，任一軌缺席（例如全雲圖幅）另一軌仍可用；
兩軌都有時，交集視為高信度、聯集視為完整範圍（見 barrier_lake.py
如何使用兩者的差異來分信心等級）。

門檻
----
SAR：Otsu 自動門檻（場景 sigma0 直方圖通常呈雙峰，水體/陸地）
NDWI：> 0.2（McFeeters 1996 原始建議值；本專案未針對台灣山區逐案校正，
       應視為起點而非定論）
面積：< 0.5 ha 的水體視為雜訊濾除（山區反光的水塘、農塘不是目標）
"""

from __future__ import annotations

import argparse

import numpy as np
from scipy.ndimage import binary_opening, generate_binary_structure
from skimage.filters import threshold_otsu
from skimage.measure import label

MIN_AREA_HA = 0.5
NDWI_THRESHOLD = 0.2


# ══════════════════════════════════════════
# SAR 低回波軌
# ══════════════════════════════════════════

def water_from_sar(sigma0_db: np.ndarray, exclude_mask: np.ndarray | None = None) -> np.ndarray:
    """
    Otsu 自動門檻切分低回波（水體）與其餘地物。

    exclude_mask（preprocess.mask.build_mask 的輸出）先排除疊掩/陰影/
    陡坡像元再算門檻，避免這些像元的極端值把 Otsu 門檻拉偏。

    >>> np.random.seed(0)
    >>> land = np.random.normal(-8, 1.5, (40, 40))
    >>> sigma0 = land.copy()
    >>> sigma0[:10, :10] = np.random.normal(-20, 1.0, (10, 10))  # 水體角落
    >>> mask = water_from_sar(sigma0)
    >>> bool(mask[5, 5])
    True
    >>> bool(mask[30, 30])
    False
    """
    valid = np.isfinite(sigma0_db)
    if exclude_mask is not None:
        valid &= ~exclude_mask
    if valid.sum() < 2 or np.nanmin(sigma0_db[valid]) == np.nanmax(sigma0_db[valid]):
        return np.zeros(sigma0_db.shape, dtype=bool)

    thresh = threshold_otsu(sigma0_db[valid])
    water = (sigma0_db <= thresh) & np.isfinite(sigma0_db)
    if exclude_mask is not None:
        water &= ~exclude_mask
    return water


# ══════════════════════════════════════════
# 光學 NDWI 軌
# ══════════════════════════════════════════

def ndwi(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """
    NDWI = (Green − NIR) / (Green + NIR)　（McFeeters 1996）

    >>> g = np.array([[0.3]]); n = np.array([[0.1]])
    >>> round(float(ndwi(g, n)[0, 0]), 3)
    0.5
    """
    g, n = green.astype("float64"), nir.astype("float64")
    denom = g + n
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom != 0, (g - n) / denom, np.nan)
    return out


def water_from_optical(green: np.ndarray, nir: np.ndarray,
                       cloud_mask: np.ndarray | None = None,
                       threshold: float = NDWI_THRESHOLD) -> np.ndarray:
    """
    NDWI > 門檻 → 水體；cloud_mask（True=雲）遮蔽的像元視為未知（回傳 False）。

    >>> g = np.full((3, 3), 0.3); n = np.full((3, 3), 0.05)
    >>> water_from_optical(g, n).all()
    np.True_
    """
    idx = ndwi(green, nir)
    water = np.nan_to_num(idx, nan=-1.0) > threshold
    if cloud_mask is not None:
        water &= ~cloud_mask
    return water


# ══════════════════════════════════════════
# 融合與清理
# ══════════════════════════════════════════

def clean_mask(mask: np.ndarray, cellsize: float, min_area_ha: float = MIN_AREA_HA) -> np.ndarray:
    """
    型態學開運算去雜訊 + 面積門檻濾除小水體。

    >>> m = np.zeros((20, 20), dtype=bool); m[0, 0] = True   # 單一雜訊像元
    >>> m[5:15, 5:15] = True                                  # 夠大的水體
    >>> out = clean_mask(m, cellsize=10.0, min_area_ha=0.5)
    >>> bool(out[0, 0])
    False
    >>> bool(out[10, 10])
    True
    """
    cleaned = binary_opening(mask, structure=generate_binary_structure(2, 1))
    min_px = max(1, int((min_area_ha * 10_000) / (cellsize ** 2)))

    labels = label(cleaned)
    if labels.max() == 0:
        return cleaned
    counts = np.bincount(labels.ravel())
    keep = np.where(counts >= min_px)[0]
    keep = keep[keep != 0]  # 0 是背景
    return np.isin(labels, keep)


def fuse(sar_water: np.ndarray | None, optical_water: np.ndarray | None) -> dict:
    """
    融合兩軌結果。

    回傳字典：
        union        — 任一軌判水體即算（範圍最大，召回優先）
        intersection — 兩軌皆判水體（信度最高，僅在兩軌都有資料時有意義）
        n_tracks     — 實際參與的軌數（1 或 2），供後續信心分級判斷用

    只有一軌時 union == 該軌結果，intersection 設為 None（無法計算）。
    """
    tracks = [t for t in (sar_water, optical_water) if t is not None]
    if not tracks:
        raise ValueError("sar_water 與 optical_water 至少要有一個")

    union = tracks[0].copy()
    for t in tracks[1:]:
        union |= t

    intersection = None
    if len(tracks) == 2:
        intersection = tracks[0] & tracks[1]

    return {"union": union, "intersection": intersection, "n_tracks": len(tracks)}


def area_ha(mask: np.ndarray, cellsize: float) -> float:
    """水體遮罩面積，公頃。"""
    return float(mask.sum()) * (cellsize ** 2) / 10_000.0


def cli() -> None:
    ap = argparse.ArgumentParser(description="由 SAR sigma0 GeoTIFF 萃取水體遮罩")
    ap.add_argument("sar", help="sigma0 GeoTIFF（dB，見 preprocess.sar）")
    ap.add_argument("out", help="輸出水體遮罩 GeoTIFF（uint8）")
    ap.add_argument("--exclude-mask", default=None, help="preprocess.mask 產生的排除遮罩")
    ap.add_argument("--min-area-ha", type=float, default=MIN_AREA_HA)
    args = ap.parse_args()

    import rasterio
    with rasterio.open(args.sar) as src:
        sigma0 = src.read(1)
        cellsize = abs(src.transform.a)
        profile = src.profile

    exclude = None
    if args.exclude_mask:
        with rasterio.open(args.exclude_mask) as m:
            exclude = m.read(1).astype(bool)

    water = water_from_sar(sigma0, exclude_mask=exclude)
    water = clean_mask(water, cellsize, args.min_area_ha)

    profile.update(dtype="uint8", count=1, nodata=None)
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(water.astype("uint8"), 1)

    print(f"✓ 已寫入 {args.out}｜水體面積 {area_ha(water, cellsize):.2f} ha")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli()
    else:
        import doctest
        fails, total = doctest.testmod()
        print(f"water: {total - fails}/{total} 通過")
