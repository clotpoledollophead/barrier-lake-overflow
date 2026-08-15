#!/usr/bin/env python3
"""
hypsometry.py — 水位–面積–容積曲線

由 DEM 對壩址上游做一次「漲水模擬」（priority-flood，由低到高依序
淹沒每個像元），邊淹邊累計面積，面積對高程積分即得容積，
一次性建立水位–容積曲線；之後每次量到水面高程或範圍，
直接查表反查容積，不必每次重算地形（見
`pipeline.attribution.forecast.volume_at` / `elevation_at`，
本檔輸出的 `[(高程, 累積容積萬m3), ...]` 格式與其直接相容）。

演算法：priority-flood（Barnes et al. 2014 的簡化版，這裡只用其
「以最小堆依高程順序展開淹沒」的核心概念，不含窪地填平的完整實作）

    1. 把出水點（pour point，通常取壩址或崩塌堵塞最低點）推入最小堆
    2. 每次彈出目前堆中高程最低的像元，標記為已淹沒、累加其面積
    3. 把它的相鄰未淹沒像元推入堆
    4. 重複直到堆空、或彈出高程超過 crest_el（壩頂高程，若有給）

依此順序彈出的每個像元，其高程即是「水位漲到多高時這個像元開始
被淹」，所以面積的累計函數天生就是對高程排序好的，直接做梯形積分
就是容積。

為何不用 richdem
-----------------
richdem 需要編譯（見 requirements.txt 註解），在部分環境裝不起來；
本檔用 Python 標準庫 heapq 實作等價的核心邏輯，量體是「壩址上游一個
山谷」等級（通常數萬像元內），效能已足夠，且沒有額外的二進位依賴。
"""

from __future__ import annotations

import argparse
import heapq

import numpy as np

NEIGHBORS_8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1),
              (0, 1), (1, -1), (1, 0), (1, 1)]


def basin_curve(dem: np.ndarray, cellsize: float, pour_point: tuple[int, int],
                crest_el: float | None = None,
                max_cells: int | None = None) -> list[tuple[float, float]]:
    """
    由出水點做 priority-flood，回傳水位–容積曲線
    `[(高程 m, 累積容積 萬m3), ...]`，由低至高、單調遞增。

    pour_point 為 (row, col)，通常取壩體堵塞處或欲評估的最低鞍部。
    crest_el 給定時，高程超過它的像元不再展開（壩頂以上不是這個
    堰塞湖的蓄水空間）；不給則淹到 DEM 圖幅邊界為止（適合先探勘
    「這個山谷最多能蓄多少水」）。

    >>> dem = np.array([
    ...     [50., 50., 50., 50., 50.],
    ...     [50., 10.,  8.,  9., 50.],
    ...     [50.,  9.,  5.,  8., 50.],
    ...     [50., 10.,  9., 10., 50.],
    ...     [50., 50., 50., 50., 50.],
    ... ])
    >>> curve = basin_curve(dem, cellsize=10.0, pour_point=(2, 2), crest_el=10.0)
    >>> curve[0]
    (5.0, 0.0)
    >>> curve[-1][0]
    10.0
    >>> curve[-1][1] > curve[0][1]
    True
    """
    n_rows, n_cols = dem.shape
    visited = np.zeros(dem.shape, dtype=bool)
    heap: list[tuple[float, int, int]] = []

    r0, c0 = pour_point
    heapq.heappush(heap, (float(dem[r0, c0]), r0, c0))

    cell_area_m2 = cellsize ** 2
    levels: list[tuple[float, int]] = []   # (高程, 累積像元數)
    n_flooded = 0

    while heap:
        el, r, c = heapq.heappop(heap)
        if visited[r, c]:
            continue
        if crest_el is not None and el > crest_el:
            continue
        if max_cells is not None and n_flooded >= max_cells:
            break

        visited[r, c] = True
        n_flooded += 1
        levels.append((el, n_flooded))

        for dr, dc in NEIGHBORS_8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < n_rows and 0 <= cc < n_cols and not visited[rr, cc]:
                heapq.heappush(heap, (float(dem[rr, cc]), rr, cc))

    if not levels:
        return [(float(dem[r0, c0]), 0.0)]

    # 面積對高程做梯形積分 → 容積（萬 m3）
    curve: list[tuple[float, float]] = []
    cum_vol_m3 = 0.0
    prev_el, prev_area_m2 = levels[0][0], 0.0
    curve.append((prev_el, 0.0))

    for el, count in levels[1:]:
        area_m2 = count * cell_area_m2
        d_el = el - prev_el
        if d_el > 0:
            cum_vol_m3 += 0.5 * (prev_area_m2 + area_m2) * d_el
            curve.append((el, round(cum_vol_m3 / 1e4, 3)))   # 萬 m3
        prev_el, prev_area_m2 = el, area_m2

    # 確保最後一點的高程等於實際淹沒的最高點（curve 已單調遞增）
    return curve


def dam_height(curve: list[tuple[float, float]]) -> float:
    """壩高（曲線最高點 − 最低點，簡化為淹沒範圍的高程落差）。"""
    if not curve:
        return 0.0
    return curve[-1][0] - curve[0][0]


def volume_at_crest(curve: list[tuple[float, float]]) -> float:
    """壩頂蓄水量（萬 m3），即曲線最高點的累積容積。"""
    return curve[-1][1] if curve else 0.0


# ══════════════════════════════════════════
# 檔案 I/O
# ══════════════════════════════════════════

def curve_from_dem_file(dem_path: str, pour_point_lonlat: tuple[float, float],
                        crest_el: float | None = None,
                        max_cells: int | None = None) -> list[tuple[float, float]]:
    """讀 GeoTIFF DEM，把經緯度出水點轉成像元座標後呼叫 basin_curve。"""
    import rasterio
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float64")
        cellsize = abs(src.transform.a)
        row, col = src.index(*pour_point_lonlat)
    return basin_curve(dem, cellsize, (row, col), crest_el=crest_el, max_cells=max_cells)


def cli() -> None:
    ap = argparse.ArgumentParser(description="由 DEM 與出水點建立水位–容積曲線")
    ap.add_argument("dem", help="DEM GeoTIFF")
    ap.add_argument("--pour-x", type=float, required=True, help="出水點 X（DEM 座標系）")
    ap.add_argument("--pour-y", type=float, required=True, help="出水點 Y（DEM 座標系）")
    ap.add_argument("--crest-el", type=float, default=None, help="壩頂高程（m），不給則淹到圖幅邊界")
    args = ap.parse_args()

    curve = curve_from_dem_file(args.dem, (args.pour_x, args.pour_y), crest_el=args.crest_el)
    print(f"共 {len(curve)} 個高程–容積控制點｜壩高約 {dam_height(curve):.1f} m｜"
          f"壩頂蓄水量約 {volume_at_crest(curve):,.0f} 萬 m3")
    for el, vol in curve:
        print(f"  {el:8.2f} m   {vol:12,.1f} 萬 m3")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli()
    else:
        import doctest
        fails, total = doctest.testmod()
        print(f"hypsometry: {total - fails}/{total} 通過")
