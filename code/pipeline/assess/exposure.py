#!/usr/bin/env python3
"""
exposure.py — 暴露評估：SEGIS 人口格網、道路中斷、孤島化聚落

三件事分開算，因為決策用途不同：
    人口暴露   — 示警文字要講「影響幾人」，直接對淹沒範圍疊人口格網加總
    道路中斷   — 判斷救災路線是否可用、需不需要改道
    孤島化聚落 — 比道路中斷更嚴重：聚落所有聯外道路都被截斷，
                救援必須靠空中或步行，優先順序最高

孤島化的判定用簡化代理（proxy），不是完整路網圖論最短路徑分析：
一個聚落點若其緩衝範圍內「所有」聯外道路路段都與淹沒範圍相交，
視為孤島化候選。這會低估孤島化（真正孤立可能要繞更遠的路才會發現
其實整個路網都斷了），但作為第一版快篩、觸發人工複核，寧可保守
（少報而非誤報），複核成本較低。
"""

from __future__ import annotations

import argparse

import numpy as np


# ══════════════════════════════════════════
# 人口暴露
# ══════════════════════════════════════════

def population_exposed(inundation_mask: np.ndarray, population: np.ndarray) -> float:
    """
    淹沒範圍內的人口加總。population 應與 inundation_mask 同網格
    （同解析度、同範圍），例如 SEGIS 100m 人口網格重採樣到 DEM 網格後的結果。

    >>> mask = np.array([[True, False], [True, True]])
    >>> pop = np.array([[10.0, 5.0], [3.0, 2.0]])
    >>> population_exposed(mask, pop)
    15.0
    """
    return float(np.nansum(population[inundation_mask]))


def population_by_band(inundation_masks_by_scenario: dict[str, np.ndarray],
                       population: np.ndarray) -> dict[str, float]:
    """
    對多個情境（如 forecast.py 的低/中/高情境對應的不同淹沒範圍）
    分別算暴露人口，回傳 {情境名: 人口}，供示警文字呈現「隨情境擴大而增加的暴露」。
    """
    return {name: population_exposed(mask, population)
            for name, mask in inundation_masks_by_scenario.items()}


# ══════════════════════════════════════════
# 道路中斷
# ══════════════════════════════════════════

def roads_interrupted_km(inundation_mask: np.ndarray, road_mask: np.ndarray,
                         cellsize: float) -> float:
    """
    道路遮罩（柵格化的路網，True=有路）與淹沒範圍的交集長度估計。

    以「相交像元數 × 像元邊長」近似道路長度，非真正沿線幾何長度
    （柵格化道路寬度通常是 1 像元，這個近似在 10–20m 解析度下
    誤差可接受；要精確長度應改用向量路網 + shapely 的
    `line.intersection(polygon).length`，見 `roads_interrupted_km_vector`）。

    >>> inun = np.array([[True, True, False]])
    >>> road = np.array([[True, False, True]])
    >>> roads_interrupted_km(inun, road, cellsize=10.0)
    0.01
    """
    hit = int((inundation_mask & road_mask).sum())
    return round(hit * cellsize / 1000.0, 4)


def roads_interrupted_km_vector(inundation_polygon, roads_gdf) -> float:
    """
    向量版：用真正的道路線幾何與淹沒範圍多邊形算交集長度（公里）。

    inundation_polygon 為 shapely 幾何（可由 bathtub_extent 的柵格
    用 rasterio.features.shapes 轉多邊形後 shapely.union_all 得到）；
    roads_gdf 為 geopandas.GeoDataFrame（路網向量圖資）。

    需要 geopandas/shapely，未安裝時明確報錯而非靜默回傳 0，
    避免誤以為「真的沒有道路中斷」。
    """
    try:
        import geopandas as gpd  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "roads_interrupted_km_vector 需要 geopandas；"
            "未安裝時請改用柵格近似版 roads_interrupted_km，"
            "或執行 pip install geopandas shapely") from exc

    clipped = roads_gdf.geometry.intersection(inundation_polygon)
    length_m = clipped.length.sum()
    return round(length_m / 1000.0, 4)


# ══════════════════════════════════════════
# 孤島化聚落（簡化代理）
# ══════════════════════════════════════════

def isolated_settlements(settlement_points: list[tuple[int, int]],
                         road_mask: np.ndarray,
                         inundation_mask: np.ndarray,
                         search_radius_px: int = 5) -> list[dict]:
    """
    對每個聚落點（row, col），檢查其搜尋半徑內的道路像元是否
    「全部」與淹沒範圍重疊；若是，判為孤島化候選。

    這是保守的代理判定（見檔頭說明），不是路網連通性分析；
    候選清單應優先進入人工複核與空拍確認，而非直接當成最終結果發布。

    >>> road = np.zeros((10, 10), dtype=bool); road[5, 3:8] = True
    >>> inun = np.zeros((10, 10), dtype=bool); inun[5, 3:8] = True   # 該路段全被淹
    >>> out = isolated_settlements([(5, 5)], road, inun, search_radius_px=3)
    >>> out[0]['isolated']
    True
    """
    n_rows, n_cols = road_mask.shape
    results = []
    for (r, c) in settlement_points:
        r0, r1 = max(0, r - search_radius_px), min(n_rows, r + search_radius_px + 1)
        c0, c1 = max(0, c - search_radius_px), min(n_cols, c + search_radius_px + 1)

        local_road = road_mask[r0:r1, c0:c1]
        local_inun = inundation_mask[r0:r1, c0:c1]

        road_px = int(local_road.sum())
        if road_px == 0:
            results.append({"point": (r, c), "isolated": False,
                           "reason": "搜尋半徑內無道路資料，無法判定"})
            continue

        cut_px = int((local_road & local_inun).sum())
        isolated = cut_px == road_px
        results.append({
            "point": (r, c),
            "isolated": isolated,
            "road_px": road_px,
            "cut_px": cut_px,
            "reason": "半徑內聯外道路全數位於淹沒範圍" if isolated
                     else f"半徑內 {road_px} 段道路中 {cut_px} 段受影響，仍有通路",
        })
    return results


# ══════════════════════════════════════════
# 彙整
# ══════════════════════════════════════════

def summarize(inundation_mask: np.ndarray, cellsize: float,
             population: np.ndarray | None = None,
             road_mask: np.ndarray | None = None,
             settlement_points: list[tuple[int, int]] | None = None) -> dict:
    """把上面幾個函式串起來，回傳可直接塞進示警文字模板的彙整結果。"""
    out: dict = {
        "inundation_area_ha": float(inundation_mask.sum()) * (cellsize ** 2) / 10_000.0,
    }
    if population is not None:
        out["population_exposed"] = population_exposed(inundation_mask, population)
    if road_mask is not None:
        out["roads_interrupted_km"] = roads_interrupted_km(inundation_mask, road_mask, cellsize)
    if road_mask is not None and settlement_points:
        isolation = isolated_settlements(settlement_points, road_mask, inundation_mask)
        out["isolated_settlements"] = [r for r in isolation if r["isolated"]]
        out["n_isolated"] = len(out["isolated_settlements"])
    return out


def cli() -> None:
    ap = argparse.ArgumentParser(description="暴露評估：人口/道路/孤島化（柵格版）")
    ap.add_argument("inundation", help="淹沒範圍 GeoTIFF（uint8，見 assess.inundation）")
    ap.add_argument("--population", default=None, help="人口網格 GeoTIFF（需與淹沒範圍同網格）")
    ap.add_argument("--roads", default=None, help="道路柵格 GeoTIFF（需與淹沒範圍同網格）")
    args = ap.parse_args()

    import rasterio
    with rasterio.open(args.inundation) as src:
        mask = src.read(1).astype(bool)
        cellsize = abs(src.transform.a)

    population = None
    if args.population:
        with rasterio.open(args.population) as src:
            population = src.read(1).astype("float64")

    road_mask = None
    if args.roads:
        with rasterio.open(args.roads) as src:
            road_mask = src.read(1).astype(bool)

    result = summarize(mask, cellsize, population=population, road_mask=road_mask)
    print(f"淹沒面積：{result['inundation_area_ha']:.2f} ha")
    if "population_exposed" in result:
        print(f"暴露人口：約 {result['population_exposed']:.0f} 人")
    if "roads_interrupted_km" in result:
        print(f"道路中斷：約 {result['roads_interrupted_km']:.2f} km")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cli()
    else:
        import doctest
        fails, total = doctest.testmod()
        print(f"exposure: {total - fails}/{total} 通過")
