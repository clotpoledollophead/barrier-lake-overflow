#!/usr/bin/env python3
"""
synthetic_dem.py — 展示用合成地形

架構文件 §07/§08（DEM 量化蓄水量、bathtub 淹沒模擬）需要真實 DEM，
但本專案目前沒有隨附任何 GeoTIFF（見 `pipeline/assess/README.md`
「尚未整合」那條——DEM 檔案本身沒有一起放進版控，一來體積太大，
二來每座湖真正需要的裁切範圍不同）。

在真實 DEM 接上之前，這裡用「山谷 + 攔阻壩」的合成地形頂著，讓
`hypsometry.py` / `inundation.py` 這兩個已經測試過的真實演算法有東西
可以跑，讓 `cap.js` 的 `area` 能先換成真正算出來的多邊形，而不是固定
半徑的 `circle`。

**地形本身完全是合成的、不是任何實測資料**。合成邏輯：

    · 一條直線河谷，方位角由湖泊 id 做確定性雜湊決定（只是讓 demo
      畫面每個湖長得不一樣，不代表真實流向——真實流向要從真實河川
      圖資或 DEM 的流向分析取得）
    · 河床沿河谷方向線性下降（模擬一般山區坡降）
    · 橫剖面為拋物線谷形（V 型谷，離中心線越遠越高，超過半寬後打平
      模擬谷緣）
    · 壩址位置疊加一道「攔阻壩脊」（模擬崩塌堵塞體），壩脊高度遠高於
      任何合理淹沒水位，確保淹沒範圍不會「滲漏」到壩體另一側——這是
      簡化的物理邊界，不是精確的壩體幾何
    · 對合成地形做 hypsometry，二分搜尋出「累積容積＝清冊登載蓄水量」
      的水位，讓合成地形至少在「蓄水量」這個唯一有官方數字可查核的
      面向上是自洽的——其餘（谷寬、坡度、壩高）都是通用預設值，
      不是這座湖的真實地形參數

呼叫端（`pipeline.assess.run`）與輸出（`dashboard/data/inundation.js`）
都必須把 `method` 標成 `"synthetic_demo_dem"`，前端必須把這個標註顯示
給使用者看，不能當成真的地形分析結果呈現。真實 DEM 接上後，這個模組
不再被呼叫，介面不用改（見 `pipeline.assess.run` 的 DEM 路徑優先序）。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_M = 6_371_000.0

DEFAULT_N_ROWS = 400
DEFAULT_N_COLS = 140
DEFAULT_CELLSIZE_M = 15.0
DEFAULT_CHANNEL_GRADIENT = 0.03       # 山區河床坡降，經驗值
DEFAULT_VALLEY_HALFWIDTH_M = 300.0
DEFAULT_VALLEY_DEPTH_M = 45.0
DEFAULT_BASE_ELEVATION_M = 800.0
DEFAULT_DAM_ROW_FRAC = 0.65
DEFAULT_DAM_RIDGE_HEIGHT_M = 140.0
DEFAULT_DAM_RIDGE_TAPER_ROWS = 6
MAX_N_ROWS = 3600                     # 安全上限（54 km），避免超大蓄水量把網格撐爆


@dataclass
class SyntheticDemResult:
    dem: np.ndarray
    cellsize: float
    origin_lat: float
    origin_lon: float
    azimuth_deg: float
    upstream_pour_rc: tuple[int, int]
    downstream_pour_rc: tuple[int, int]
    ridge_crest_el: float

    def rowcol_to_lonlat(self, row: float, col: float) -> tuple[float, float]:
        """把合成網格座標轉回經緯度（近似 ENU 投影，跟本專案其他模組
        `catchment.py`／`dispatch.py` 用的 111km/度近似法一致）。
        用壩址（攔阻壩脊中心）當地理錨點——這是本合成地形唯一一個
        對應到真實座標（清冊登載的壩址經緯度）的點。"""
        dam_col = self.dem.shape[1] // 2
        dam_row_anchor = _dam_row_from_result(self)
        along_m = (row - dam_row_anchor) * self.cellsize
        cross_m = (col - dam_col) * self.cellsize

        az = math.radians(self.azimuth_deg)
        north_m = along_m * math.cos(az) - cross_m * math.sin(az)
        east_m = along_m * math.sin(az) + cross_m * math.cos(az)

        dlat = north_m / EARTH_RADIUS_M * (180.0 / math.pi)
        dlon = (east_m / (EARTH_RADIUS_M * max(math.cos(math.radians(self.origin_lat)), 1e-6))
                * (180.0 / math.pi))
        return (round(self.origin_lon + dlon, 6), round(self.origin_lat + dlat, 6))


def _dam_row_from_result(result: SyntheticDemResult) -> int:
    # downstream_pour_rc 與 upstream_pour_rc 對稱夾住壩脊，中點即壩脊列。
    return (result.upstream_pour_rc[0] + result.downstream_pour_rc[0]) // 2


def hash_azimuth_deg(lake_id: str) -> float:
    """用湖泊 id 做確定性雜湊決定合成河谷方位角（0-359 度，正北為 0）。
    純粹讓不同湖泊的 demo 畫面看起來不同，不代表真實流向。

    >>> 0 <= hash_azimuth_deg("bl071") < 360
    True
    >>> hash_azimuth_deg("bl071") == hash_azimuth_deg("bl071")
    True
    """
    h = int(hashlib.sha1(lake_id.encode("utf-8")).hexdigest(), 16)
    return float(h % 360)


def _build_dem(n_rows: int, n_cols: int, cellsize: float,
                channel_gradient: float, valley_halfwidth_m: float,
                valley_depth_m: float, base_elevation_m: float,
                dam_row: int, dam_ridge_height_m: float,
                dam_ridge_taper_rows: int) -> np.ndarray:
    rows = np.arange(n_rows).reshape(-1, 1).astype("float64")
    cols = np.arange(n_cols).reshape(1, -1).astype("float64")
    center_col = n_cols / 2.0

    channel_bed = base_elevation_m - channel_gradient * cellsize * rows
    cross_frac = np.minimum(1.0, np.abs((cols - center_col) * cellsize) / valley_halfwidth_m)
    cross_shape = valley_depth_m * cross_frac ** 2

    dem = channel_bed + cross_shape  # broadcast → (n_rows, n_cols)

    ridge_bump = dam_ridge_height_m * np.maximum(
        0.0, 1.0 - np.abs(rows - dam_row) / max(dam_ridge_taper_rows, 1))
    dem = dem + ridge_bump  # 壩脊橫跨整個谷寬，只沿河谷方向（列）漸變

    return dem


def build_synthetic_valley_dem(
    lake_id: str, dam_lat: float, dam_lon: float,
    n_rows: int = DEFAULT_N_ROWS, n_cols: int = DEFAULT_N_COLS,
    cellsize: float = DEFAULT_CELLSIZE_M,
    channel_gradient: float = DEFAULT_CHANNEL_GRADIENT,
    valley_halfwidth_m: float = DEFAULT_VALLEY_HALFWIDTH_M,
    valley_depth_m: float = DEFAULT_VALLEY_DEPTH_M,
    base_elevation_m: float = DEFAULT_BASE_ELEVATION_M,
    dam_row_frac: float = DEFAULT_DAM_ROW_FRAC,
    dam_ridge_height_m: float = DEFAULT_DAM_RIDGE_HEIGHT_M,
    dam_ridge_taper_rows: int = DEFAULT_DAM_RIDGE_TAPER_ROWS,
) -> SyntheticDemResult:
    """建立合成山谷 + 攔阻壩地形。

    >>> r = build_synthetic_valley_dem("bl071", 23.70061, 121.29752, n_rows=100, n_cols=60)
    >>> r.dem.shape
    (100, 60)
    >>> r.ridge_crest_el > r.dem[r.upstream_pour_rc]
    True
    """
    dam_row = int(n_rows * dam_row_frac)
    center_col = n_cols // 2

    dem = _build_dem(n_rows, n_cols, cellsize, channel_gradient, valley_halfwidth_m,
                      valley_depth_m, base_elevation_m, dam_row,
                      dam_ridge_height_m, dam_ridge_taper_rows)

    margin = dam_ridge_taper_rows + 2
    upstream_row = max(0, dam_row - margin)
    downstream_row = min(n_rows - 1, dam_row + margin)

    ridge_crest_el = float(dem[dam_row, center_col])

    azimuth = hash_azimuth_deg(lake_id)

    return SyntheticDemResult(
        dem=dem,
        cellsize=cellsize,
        origin_lat=dam_lat,
        origin_lon=dam_lon,
        azimuth_deg=azimuth,
        upstream_pour_rc=(upstream_row, center_col),
        downstream_pour_rc=(downstream_row, center_col),
        ridge_crest_el=ridge_crest_el,
    )


def grow_until_capacity(lake_id: str, dam_lat: float, dam_lon: float,
                         target_volume_wan_m3: float,
                         start_n_rows: int = DEFAULT_N_ROWS,
                         **kwargs) -> tuple[SyntheticDemResult, list[tuple[float, float]]]:
    """反覆放大合成地形的河谷長度，直到壩前容量（curve 最高點的累積容積）
    足以容納清冊登載的蓄水量，或碰到 MAX_N_ROWS 安全上限為止。

    回傳 (地形, 水位–容積曲線)；curve 已經是壩前（crest_el 以下）的完整曲線，
    找目標水位時直接對這條曲線內插即可，不用重算地形。
    """
    from pipeline.assess.hypsometry import basin_curve

    n_rows = start_n_rows
    while True:
        result = build_synthetic_valley_dem(lake_id, dam_lat, dam_lon,
                                             n_rows=n_rows, **kwargs)
        curve = basin_curve(
            result.dem, result.cellsize, result.upstream_pour_rc,
            crest_el=result.ridge_crest_el - 0.1,
        )
        capacity = curve[-1][1] if curve else 0.0
        if capacity >= target_volume_wan_m3 or n_rows >= MAX_N_ROWS:
            return result, curve
        n_rows = min(MAX_N_ROWS, int(n_rows * 1.6) + 20)


if __name__ == "__main__":
    import doctest
    n_fail, n_run = doctest.testmod()
    print(f"doctest: {n_run - n_fail}/{n_run} passed")
