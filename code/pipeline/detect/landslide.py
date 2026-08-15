#!/usr/bin/env python3
"""
landslide.py — 崩塌變化偵測：振幅比值法（主力）+ 相干性變化法（升級）

TRACK A — 振幅比值（GRD，主力）
--------------------------------
事件前後兩景 sigma0（dB）相減。裸露崩塌地失去植被覆蓋，
後向散射特性改變，通常呈現「震前低回波 → 震後高回波」
（粗糙裸露地表比植被冠層更容易產生強烈漫散射）或反向劇烈變化，
取絕對值變化量門檻化即可，不需要方向假設。

入射角 > 20° 的場景邊緣幾何失真較大，建議先用
preprocess.mask 的坡度/疊掩遮罩排除，本檔的 exclude_mask
參數即接該輸出。

TRACK B — 相干性變化（SLC，升級路徑）
--------------------------------------
干涉相干性（coherence）量測兩次過境地表散射的穩定程度；
崩塌造成地表結構劇變，相干性會驟降。相干性計算本身需要
SLC 影像的複數共軛相乘、多視、去相位噪声等步驟，運算量遠高於
GRD 振幅比值，本檔僅提供「已算好相干性圖」之後的變化判定
（coherence_drop），相干性本身的計算留待 TWCC 算力到位後
接入（見系統簡報 TRACK A/B 說明）。

崩塌 vs 河道地形的區辨
-----------------------
振幅劇變也可能來自：河道水位暴漲、道路施工、雲影。
本檔只做「變化偵測」，不做「崩塌 vs 其他變化」的語意分類——
後者需要疊圖坡度（陡坡才可能是崩塌，見 clean_by_slope）、
崩塌潛勢圖層、以及人工複核，屬 barrier_lake.py 與後續工作範圍。
"""

from __future__ import annotations

import argparse

import numpy as np
from scipy.ndimage import binary_opening, generate_binary_structure
from skimage.measure import label

MIN_AREA_HA = 0.5
DEFAULT_AMPLITUDE_DELTA_DB = 3.0   # 振幅比值法：|Δsigma0| 門檻
DEFAULT_COHERENCE_DROP = 0.3       # 相干性法：coherence 下降量門檻


# ══════════════════════════════════════════
# TRACK A — 振幅比值
# ══════════════════════════════════════════

def amplitude_change(sigma0_pre_db: np.ndarray, sigma0_post_db: np.ndarray) -> np.ndarray:
    """
    事件後 − 事件前（dB）。正值：回波變強（常見於裸露崩塌地）；
    負值：回波變弱（常見於崩積土掩埋植被、或新生水體）。

    >>> pre = np.array([[-10.0, -10.0]])
    >>> post = np.array([[-4.0, -10.0]])
    >>> float(amplitude_change(pre, post)[0, 0])
    6.0
    """
    return sigma0_post_db.astype("float64") - sigma0_pre_db.astype("float64")


def landslide_from_amplitude(sigma0_pre_db: np.ndarray, sigma0_post_db: np.ndarray,
                             threshold_db: float = DEFAULT_AMPLITUDE_DELTA_DB,
                             exclude_mask: np.ndarray | None = None) -> np.ndarray:
    """
    |Δsigma0| > threshold_db → 疑似崩塌變化像元。

    >>> pre = np.full((3, 3), -10.0)
    >>> post = pre.copy(); post[0, 0] = -2.0
    >>> mask = landslide_from_amplitude(pre, post, threshold_db=3.0)
    >>> bool(mask[0, 0])
    True
    >>> bool(mask[1, 1])
    False
    """
    delta = amplitude_change(sigma0_pre_db, sigma0_post_db)
    mask = np.abs(delta) > threshold_db
    mask &= np.isfinite(delta)
    if exclude_mask is not None:
        mask &= ~exclude_mask
    return mask


# ══════════════════════════════════════════
# TRACK B — 相干性變化（升級路徑，接已算好的相干性圖）
# ══════════════════════════════════════════

def landslide_from_coherence(coherence_pre_event: np.ndarray, coherence_post_event: np.ndarray,
                             drop_threshold: float = DEFAULT_COHERENCE_DROP,
                             exclude_mask: np.ndarray | None = None) -> np.ndarray:
    """
    coherence_pre_event：事件前一對過境算出的相干性（地表本應穩定，基準值）
    coherence_post_event：跨事件（事件前-事件後）算出的相干性

    下降量 = coherence_pre_event − coherence_post_event 越大，
    代表事件造成的地表結構改變越劇烈。

    相干性本身（複數影像共軛相乘、多視平均）不在本檔範圍內，
    見檔頭 TRACK B 說明。

    >>> pre = np.full((2, 2), 0.8)
    >>> post = pre.copy(); post[0, 0] = 0.2
    >>> landslide_from_coherence(pre, post, drop_threshold=0.3)[0, 0]
    np.True_
    """
    drop = coherence_pre_event.astype("float64") - coherence_post_event.astype("float64")
    mask = drop > drop_threshold
    mask &= np.isfinite(drop)
    if exclude_mask is not None:
        mask &= ~exclude_mask
    return mask


# ══════════════════════════════════════════
# 清理：坡度篩選 + 面積門檻
# ══════════════════════════════════════════

def clean_by_slope(mask: np.ndarray, slope_deg: np.ndarray, min_slope_deg: float = 15.0) -> np.ndarray:
    """
    崩塌通常發生在有一定坡度的地方；平坦處的劇烈回波變化
    多半是河道改道、施工、或雲影，不是崩塌。

    >>> mask = np.array([[True, True]])
    >>> slope = np.array([[5.0, 30.0]])
    >>> clean_by_slope(mask, slope, min_slope_deg=15.0)[0].tolist()
    [False, True]
    """
    return mask & (slope_deg >= min_slope_deg)


def clean_mask(mask: np.ndarray, cellsize: float, min_area_ha: float = MIN_AREA_HA) -> np.ndarray:
    """型態學開運算去雜訊 + 面積門檻濾除小範圍雜訊（同 detect.water.clean_mask）。"""
    cleaned = binary_opening(mask, structure=generate_binary_structure(2, 1))
    min_px = max(1, int((min_area_ha * 10_000) / (cellsize ** 2)))

    labels = label(cleaned)
    if labels.max() == 0:
        return cleaned
    counts = np.bincount(labels.ravel())
    keep = np.where(counts >= min_px)[0]
    keep = keep[keep != 0]
    return np.isin(labels, keep)


def area_ha(mask: np.ndarray, cellsize: float) -> float:
    return float(mask.sum()) * (cellsize ** 2) / 10_000.0


def cli() -> None:
    ap = argparse.ArgumentParser(description="事件前後 sigma0 GeoTIFF 做振幅比值崩塌變化偵測")
    ap.add_argument("pre", help="事件前 sigma0 GeoTIFF（dB）")
    ap.add_argument("post", help="事件後 sigma0 GeoTIFF（dB）")
    ap.add_argument("out", help="輸出崩塌變化遮罩 GeoTIFF（uint8）")
    ap.add_argument("--dem", default=None, help="DEM，給了才做坡度篩選")
    ap.add_argument("--threshold-db", type=float, default=DEFAULT_AMPLITUDE_DELTA_DB)
    ap.add_argument("--min-slope-deg", type=float, default=15.0)
    ap.add_argument("--min-area-ha", type=float, default=MIN_AREA_HA)
    args = ap.parse_args()

    import rasterio
    with rasterio.open(args.pre) as src:
        pre = src.read(1)
        cellsize = abs(src.transform.a)
        profile = src.profile
    with rasterio.open(args.post) as src:
        post = src.read(1)

    mask = landslide_from_amplitude(pre, post, threshold_db=args.threshold_db)

    if args.dem:
        from pipeline.preprocess.mask import slope_aspect
        with rasterio.open(args.dem) as src:
            dem = src.read(1).astype("float64")
        slope, _ = slope_aspect(dem, cellsize)
        mask = clean_by_slope(mask, slope, args.min_slope_deg)

    mask = clean_mask(mask, cellsize, args.min_area_ha)

    profile.update(dtype="uint8", count=1, nodata=None)
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(mask.astype("uint8"), 1)

    print(f"✓ 已寫入 {args.out}｜崩塌變化面積 {area_ha(mask, cellsize):.2f} ha")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli()
    else:
        import doctest
        fails, total = doctest.testmod()
        print(f"landslide: {total - fails}/{total} 通過")
