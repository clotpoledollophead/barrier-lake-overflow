#!/usr/bin/env python3
"""
catchment.py — 觸發後的「圈定集水區」（架構文件 §04 / §02 FIG.1「圈定集水區」）

架構文件寫的是「潛勢圖層 × 震度/雨量」疊合出真正的集水區邊界（依水利署
集水區圖資），但那需要向量圖資（shapely/geopandas，見 pyproject.toml
的 `vector` 選用相依套件，本專案 requirements.txt 尚未強制安裝）。

本檔先用最小可行版本頂著，介面設計成「之後把真正的分水嶺 polygon
換進來也不用改呼叫端」：

    1. circle_aoi()       —— 用地理半徑近似集水區範圍（簡化，非真實分水嶺）
    2. lakes_in_aoi()      —— 疊合本專案既有的堰塞湖清冊（dashboard/data/lakes.js
                              解析出的 dict list），標出哪些已知湖泊落在圈定範圍內
                              ——這是目前唯一可信的「優先順序」依據，因為它是
                              真實地理資料，不是近似值
    3. slope_filter_aoi()  —— 有 DEM 時才啟用，重用 preprocess/mask.py 的
                              slope_aspect()，篩出坡度 > 30° 的範圍
                              （地震觸發規則要求的那一步）；沒有 DEM 時
                              直接跳過，回傳 None 並在 AOI 上誠實標註
                              "slope_filtered": False

TODO（下一步要接的地方，不是還沒開始想）：真正的集水區向量圖資
（水利署河川圖籍 WMTS/圖層）介接後，circle_aoi() 的回傳值應該換成
polygon 交集後的真實集水區範圍；lakes_in_aoi() 與後續 preprocess/detect
的呼叫介面不需要跟著改，因為它們吃的都是 CatchmentAOI 這個型別而不是
畫圓的細節。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """兩點球面距離（公里）。

    >>> round(haversine_km(23.67, 121.42, 23.67, 121.42), 3)
    0.0
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class CatchmentAOI:
    """簡化版集水區範圍：目前是一個圓，未來換成真實分水嶺 polygon 時
    這個型別的欄位可以保留、加欄位即可，呼叫端不需要大改。"""
    center_lat: float
    center_lon: float
    radius_km: float
    method: str = "circle_buffer"          # 之後真實圈定改成 "watershed_polygon"
    slope_filtered: bool = False           # 是否已套用 DEM 坡度篩選
    label: Optional[str] = None            # 人看得懂的名稱（如「震央 30km」）


def circle_aoi(lat: float, lon: float, radius_km: float,
               label: Optional[str] = None) -> CatchmentAOI:
    """用地理半徑畫一個簡化版集水區範圍。

    >>> aoi = circle_aoi(23.67, 121.42, 30.0)
    >>> aoi.radius_km
    30.0
    """
    return CatchmentAOI(center_lat=lat, center_lon=lon, radius_km=radius_km, label=label)


def polygon_ring(aoi: CatchmentAOI, n_points: int = 32) -> list[tuple[float, float]]:
    """把圓形 AOI 展開成 n 個頂點的近似多邊形（(lat, lon) 序列，首尾相接），
    供前端地圖或未來輸出 GeoJSON 用。

    >>> ring = polygon_ring(circle_aoi(23.67, 121.42, 10.0), n_points=4)
    >>> len(ring)
    4
    """
    points = []
    # 緯度 1 度 ≈ 111 km；經度 1 度隨緯度縮放，簡化但在 30km 級距誤差可接受。
    dlat_per_km = 1.0 / 111.0
    dlon_per_km = 1.0 / (111.0 * max(math.cos(math.radians(aoi.center_lat)), 1e-6))
    for i in range(n_points):
        theta = 2 * math.pi * i / n_points
        dlat = aoi.radius_km * math.sin(theta) * dlat_per_km
        dlon = aoi.radius_km * math.cos(theta) * dlon_per_km
        points.append((round(aoi.center_lat + dlat, 5), round(aoi.center_lon + dlon, 5)))
    return points


def lakes_in_aoi(aoi: CatchmentAOI, lakes: list[dict]) -> list[dict]:
    """疊合既有堰塞湖清冊（dashboard/data/lakes.js 解析結果），
    回傳落在 AOI 半徑內的湖泊，依距離由近到遠排序，並附上距離。

    這是目前唯一「真實資料」的圈定依據：地震/豪雨觸發後，
    範圍內已知的湖泊（尤其 statusKey == "watch"）應優先派工調度影像，
    不用等真正的集水區向量圖資接上才有排序依據。

    >>> aoi = circle_aoi(23.67, 121.42, 30.0)
    >>> lakes = [{"name": "馬太鞍溪堰塞湖", "lat": 23.68, "lon": 121.43, "statusKey": "watch"}]
    >>> len(lakes_in_aoi(aoi, lakes))
    1
    """
    hits = []
    for lk in lakes:
        lat, lon = lk.get("lat"), lk.get("lon")
        if lat is None or lon is None:
            continue
        d = haversine_km(aoi.center_lat, aoi.center_lon, float(lat), float(lon))
        if d <= aoi.radius_km:
            hit = dict(lk)
            hit["distance_km"] = round(d, 1)
            hits.append(hit)
    hits.sort(key=lambda h: h["distance_km"])
    return hits


def slope_filter_aoi(aoi: CatchmentAOI, dem, cellsize: float,
                      min_slope_deg: float = 30.0):
    """有 DEM 陣列時，篩出 AOI（近似方框裁切後的 dem）中坡度 > min_slope_deg
    的範圍，回傳 (mask, area_ha)；沒有 DEM 時回傳 (None, None) 並讓呼叫端
    自行決定要不要繼續（不強制要求 DEM，因為觸發當下不一定已經下載好）。

    重用 pipeline.preprocess.mask.slope_aspect，不重新刻一份坡度演算法。
    """
    if dem is None:
        return None, None
    from pipeline.preprocess.mask import slope_aspect

    slope_deg, _aspect = slope_aspect(dem, cellsize)
    mask = slope_deg > min_slope_deg
    area_ha = float(mask.sum()) * (cellsize ** 2) / 10_000.0
    aoi.slope_filtered = True
    return mask, area_ha


if __name__ == "__main__":
    import doctest
    n_fail, n_run = doctest.testmod()
    print(f"doctest: {n_run - n_fail}/{n_run} passed")
