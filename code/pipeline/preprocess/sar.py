#!/usr/bin/env python3
"""
sar.py — Sentinel-1 IW GRD 前處理鏈

標準鏈（對照 SNAP Graph Builder 的四步驟）：
    1. 軌道精化（Apply Orbit File）—— 需 SNAP/snappy 存取 POEORB/RESORB，
       本檔不重做，交由 SNAP 產出的中繼 GeoTIFF 作為輸入。
    2. 輻射校正（Calibration）—— DN → sigma0，本檔用簡化公式實作
       （見 calibrate()），可離開 SNAP 獨立驗證；正式產出建議仍走
       SNAP 的 Calibrate operator（使用逐像元查校表，精度較高）。
    3. 斑點抑制（Speckle Filter，可選）—— Lee-like 中值濾波近似。
    4. 地形校正（Terrain Correction）—— 重投影至 EPSG:3826，
       本檔用 DEM 做簡化正射校正（reprojection，非完整 RTC）。

為什麼不整條都用 snappy
------------------------
`esa-snappy` 需要先裝 SNAP 本體且體積龐大，team 成員環境不一定裝得起來
（見 requirements.txt 註解）。本檔把「輻射校正公式」與「重投影」抽成
不依賴 snappy 的純 Python 實作，讓沒裝 SNAP 的人也能跑通流程、寫測試；
snappy 可用時，直接把它的四步驟輸出接到 calibrate() 之後（或跳過
calibrate，因為 snappy 已校正過）即可。

用法（於 code/ 目錄下）：
    python -m pipeline.preprocess.sar \
        raw_grd.tif calibrated.tif --to-db
"""

from __future__ import annotations

import argparse

import numpy as np

# Sentinel-1 IW GRD 沒有逐像元查校表時的近似常數（dB），
# 僅供 offline 驗證流程用；正式產出仍需場景專屬的 calibration LUT
# （SNAP Calibrate operator 或 manifest 內的 calibrationVector）。
DEFAULT_CALIBRATION_CONSTANT_DB = -83.0


def calibrate(dn: np.ndarray, calibration_constant_db: float = DEFAULT_CALIBRATION_CONSTANT_DB,
              to_db: bool = True) -> np.ndarray:
    """
    DN → sigma0（雷達後向散射係數）。

    簡化公式：sigma0_dB = 20*log10(DN) + calibration_constant_db
    （忽略距離向增益變化，假設校正常數已包含該場景平均項；
    真正逐像元校正需要 calibration LUT，見檔頭說明。）

    >>> dn = np.array([[100.0, 1000.0]])
    >>> out = calibrate(dn, calibration_constant_db=-83.0, to_db=True)
    >>> round(float(out[0, 0]), 2)
    -43.0
    """
    dn = np.asarray(dn, dtype="float64")
    dn_safe = np.where(dn > 0, dn, np.nan)
    sigma0_db = 20.0 * np.log10(dn_safe) + calibration_constant_db
    if to_db:
        return sigma0_db
    return 10.0 ** (sigma0_db / 10.0)  # 線性尺度


def despeckle(sigma0: np.ndarray, size: int = 3) -> np.ndarray:
    """
    斑點抑制：中值濾波近似 Lee filter 的平滑效果。

    真正的 Lee filter 會依局部變異數加權，比單純中值濾波保邊緣；
    這裡先用中值濾波求快，AOI 小時人眼與統計量已夠用；
    要換 Lee/Refined-Lee 可在此函式內部替換實作，介面不變。

    >>> import numpy as np
    >>> a = np.array([[1., 1., 1.], [1., 99., 1.], [1., 1., 1.]])
    >>> float(despeckle(a, size=3)[1, 1])
    1.0
    """
    from scipy.ndimage import median_filter
    return median_filter(sigma0, size=size)


def terrain_correct(sigma0: np.ndarray, src_transform, src_crs,
                    dem_path: str, dst_crs: str = "EPSG:3826",
                    resolution: float | None = None):
    """
    地形校正（簡化版）：把影像重投影到 dst_crs，格網對齊到 DEM。

    這是「重投影 + 對齊 DEM 網格」，不是完整的 Radiometric Terrain
    Correction（後者還要用 DEM 算局部入射角來做輻射量的地形正規化，
    見 preprocess.mask.local_incidence_angle，該函式已可覆用於此）。
    對「崩塌／水體變化偵測」這種比較前後兩景相對變化的用途，
    重投影對齊通常已足夠；若要拿 sigma0 的絕對值做跨場景比較，
    才需要補上完整 RTC。

    回傳 (array, transform)，其座標網格與 dem_path 一致，
    方便後續直接與 DEM 疊圖。
    """
    import rasterio
    from rasterio.warp import reproject, Resampling

    with rasterio.open(dem_path) as dem_src:
        dst_transform = dem_src.transform
        dst_shape = (dem_src.height, dem_src.width)
        if dst_crs is None:
            dst_crs = dem_src.crs

    dst = np.full(dst_shape, np.nan, dtype="float64")
    reproject(
        source=sigma0,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.bilinear,
    )
    return dst, dst_transform


# ══════════════════════════════════════════
# 檔案 I/O 與整條鏈
# ══════════════════════════════════════════

def process_grd(src_path: str, dst_path: str, dem_path: str | None = None,
                calibration_constant_db: float = DEFAULT_CALIBRATION_CONSTANT_DB,
                speckle_filter: bool = True, to_db: bool = True) -> None:
    """讀原始 GRD DN GeoTIFF → 校正（→ 去斑）（→ 地形校正）→ 寫出。"""
    import rasterio

    with rasterio.open(src_path) as src:
        dn = src.read(1)
        profile = src.profile
        transform, crs = src.transform, src.crs

    sigma0 = calibrate(dn, calibration_constant_db, to_db=to_db)
    if speckle_filter:
        sigma0 = despeckle(sigma0)

    if dem_path:
        sigma0, transform = terrain_correct(sigma0, transform, crs, dem_path)
        profile.update(height=sigma0.shape[0], width=sigma0.shape[1],
                       transform=transform, crs="EPSG:3826")

    profile.update(dtype="float32", count=1, nodata=np.nan)
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(sigma0.astype("float32"), 1)


def cli() -> None:
    ap = argparse.ArgumentParser(description="Sentinel-1 GRD 前處理鏈（校正/去斑/地形校正）")
    ap.add_argument("src", help="原始 GRD DN GeoTIFF")
    ap.add_argument("dst", help="輸出 sigma0 GeoTIFF")
    ap.add_argument("--dem", default=None, help="地形校正用 DEM（不給則跳過此步）")
    ap.add_argument("--calib-const", type=float, default=DEFAULT_CALIBRATION_CONSTANT_DB)
    ap.add_argument("--no-speckle-filter", action="store_true")
    ap.add_argument("--linear", action="store_true", help="輸出線性尺度而非 dB")
    args = ap.parse_args()

    process_grd(args.src, args.dst, dem_path=args.dem,
               calibration_constant_db=args.calib_const,
               speckle_filter=not args.no_speckle_filter,
               to_db=not args.linear)
    print(f"✓ 已寫入 {args.dst}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli()
    else:
        import doctest
        fails, total = doctest.testmod()
        print(f"sar: {total - fails}/{total} 通過")
