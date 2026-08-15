#!/usr/bin/env python3
"""
inundation.py — 淹沒模擬（bathtub model）

先做 bathtub model 快估：假設水面為水平面，從出水點（潰壩點/溢流點）
沿地形連通展開，凡是「連通且高程 ≤ 給定水位」的像元都算淹沒範圍。
這是災防上「先求快、有個保守估計可以先示警」的做法，忽略流速、
波前推進時間、河道輸砂等動力過程；README 已明列「再升級簡化一維
水動力（HEC-RAS 等）」為下一步，本檔先把 v1 做完整、做對。

限制展開距離
------------
下游淹沒模擬限制在出水點下游 `max_distance_km`（預設 2 km，
對應系統簡報的既定假設）以內，理由：
    · bathtub model 沒有流量衰減的概念，模擬距離越長，
      「連通即淹沒」的假設越不合理（現實中水量有限、會沿途消散）
    · 中下游地形變化大，2 km 外的地形通常已超出堰塞湖蓄水量
      合理能達到的範圍，繼續展開只會沿著低窪地形不合理地蔓延

連通展開用 BFS（4-連通，避免對角線「穿過」地形稜線造成不合理蔓延），
每步累積沿路徑走過的水平距離，超過門檻就不繼續往外展開
（但已展開的像元仍保留在淹沒範圍內）。
"""

from __future__ import annotations

import argparse
from collections import deque

import numpy as np

NEIGHBORS_4 = [(-1, 0), (1, 0), (0, -1), (0, 1)]
DEFAULT_MAX_DISTANCE_KM = 2.0


def bathtub_extent(dem: np.ndarray, cellsize: float, pour_point: tuple[int, int],
                   water_el: float,
                   max_distance_km: float = DEFAULT_MAX_DISTANCE_KM) -> np.ndarray:
    """
    由出水點做 BFS 連通展開，回傳布林淹沒遮罩。

    只有「高程 ≤ water_el 且與出水點路徑連通」的像元才算淹沒——
    這避免了單純用 `dem <= water_el` 造成的假陽性：地圖上其他
    互不相連、恰好低於水位的窪地不該被算進同一場淹水。

    >>> dem = np.array([
    ...     [20., 20., 20., 20.],
    ...     [20.,  5.,  4.,  6.],
    ...     [20.,  6.,  5., 20.],
    ...     [20., 20., 20., 20.],
    ... ])
    >>> mask = bathtub_extent(dem, cellsize=10.0, pour_point=(1, 1), water_el=6.0)
    >>> bool(mask[1, 1]), bool(mask[1, 2]), bool(mask[0, 0])
    (True, True, False)
    """
    n_rows, n_cols = dem.shape
    r0, c0 = pour_point
    if dem[r0, c0] > water_el:
        return np.zeros(dem.shape, dtype=bool)

    max_distance_m = max_distance_km * 1000.0
    flooded = np.zeros(dem.shape, dtype=bool)
    dist = np.zeros(dem.shape, dtype="float64")

    flooded[r0, c0] = True
    q = deque([(r0, c0)])

    while q:
        r, c = q.popleft()
        for dr, dc in NEIGHBORS_4:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < n_rows and 0 <= cc < n_cols):
                continue
            if flooded[rr, cc]:
                continue
            if dem[rr, cc] > water_el:
                continue
            step_dist = dist[r, c] + cellsize
            if step_dist > max_distance_m:
                continue
            flooded[rr, cc] = True
            dist[rr, cc] = step_dist
            q.append((rr, cc))

    return flooded


def extent_area_ha(mask: np.ndarray, cellsize: float) -> float:
    return float(mask.sum()) * (cellsize ** 2) / 10_000.0


def max_depth(dem: np.ndarray, mask: np.ndarray, water_el: float) -> float:
    """淹沒範圍內的最大水深（m）＝ water_el − 最低點高程。"""
    if not mask.any():
        return 0.0
    return float(water_el - np.min(dem[mask]))


# ══════════════════════════════════════════
# 檔案 I/O
# ══════════════════════════════════════════

def extent_from_dem_file(dem_path: str, pour_point_xy: tuple[float, float],
                         water_el: float,
                         max_distance_km: float = DEFAULT_MAX_DISTANCE_KM):
    """讀 GeoTIFF DEM，回傳 (mask, profile)。"""
    import rasterio
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float64")
        cellsize = abs(src.transform.a)
        row, col = src.index(*pour_point_xy)
        profile = src.profile
    mask = bathtub_extent(dem, cellsize, (row, col), water_el, max_distance_km)
    profile.update(dtype="uint8", count=1, nodata=None)
    return mask, profile


def cli() -> None:
    ap = argparse.ArgumentParser(description="以 bathtub model 模擬淹沒範圍")
    ap.add_argument("dem", help="DEM GeoTIFF")
    ap.add_argument("out", help="輸出淹沒範圍 GeoTIFF（uint8）")
    ap.add_argument("--pour-x", type=float, required=True)
    ap.add_argument("--pour-y", type=float, required=True)
    ap.add_argument("--water-el", type=float, required=True, help="模擬水位（m）")
    ap.add_argument("--max-distance-km", type=float, default=DEFAULT_MAX_DISTANCE_KM)
    args = ap.parse_args()

    mask, profile = extent_from_dem_file(
        args.dem, (args.pour_x, args.pour_y), args.water_el, args.max_distance_km)

    import rasterio
    with rasterio.open(args.out, "w", **profile) as dst:
        dst.write(mask.astype("uint8"), 1)

    with rasterio.open(args.dem) as src:
        dem = src.read(1).astype("float64")
        cellsize = abs(src.transform.a)

    print(f"✓ 已寫入 {args.out}｜淹沒面積 {extent_area_ha(mask, cellsize):.2f} ha"
          f"｜最大水深 {max_depth(dem, mask, args.water_el):.1f} m")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli()
    else:
        import doctest
        fails, total = doctest.testmod()
        print(f"inundation: {total - fails}/{total} 通過")
