#!/usr/bin/env python3
"""
run.py — assess 管線總入口（架構文件 §07 蓄水量、§08 淹沒模擬）

把已經各自測試過的 `hypsometry` / `inundation` 接成一條「給一筆湖泊紀錄，
吐出淹沒多邊形」的流程，取代 `dashboard/cap.js` 目前用的固定半徑
`circle`。DEM 來源依下列優先序：

    1. 真實 DEM：`data/raw/dem/<lakeId>.tif`（單一湖泊裁切好的 GeoTIFF）
       或 `data/raw/dem/taiwan_dem.tif`（全台 DEM，用清冊座標裁切）。
       本專案目前兩者都沒有隨附（見 `data/README.md`），程式邏輯已經
       寫好、也有走檔案 I/O 的路徑，DEM 送達後不用改介面。
    2. 合成地形（`pipeline.assess.synthetic_dem`）：真實 DEM 不存在時的
       demo 備援，**必須顯式加 `--demo-dem` 才會啟用**（見
       `pipeline.build_all`），輸出一律標註
       `method: "synthetic_demo_dem"`，前端必須把這個標註顯示出來。

只處理清冊 `statusKey == "watch"`（監測中）的湖泊——已消失／已穩定的
湖不需要淹沒模擬，這點跟 `dashboard/cap.js` 現有的把關邏輯（gone/stable/
watch）一致，不要在這裡重新做一次不同的篩選標準。

尚未整合（誠實列出，不是這次的範圍）：
    · exposure.py（人口/道路暴露）——需要 SEGIS 人口網格與水利署道路
      圖資，本專案沒有隨附也沒有合成版本；用合成地形去疊合成人口/
      道路會是兩層假資料疊在一起，不確定性會複合到失去意義，所以
      這裡刻意不做，等真實資料到位再接。
    · preprocess/detect（SAR 水體/崩塌偵測）——需要真實 Sentinel-1
      影像，這裡完全沒有涉及，也不應該用合成影像頂替（那會偽造
      「衛星有偵測到」這件事本身，跟本檔用合成地形頂淹沒範圍在
      誠實程度上是不同等級的事）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

from pipeline.assess import hypsometry, inundation

DEFAULT_DEM_DIR = "../data/raw/dem"
DEFAULT_MAX_DISTANCE_KM = inundation.DEFAULT_MAX_DISTANCE_KM


@dataclass
class AssessResult:
    lake_id: str
    lake_name: str
    method: str                    # "real_dem" | "synthetic_demo_dem"
    area_ha: float
    max_depth_m: float
    dam_height_m: float
    volume_at_target_wan_m3: float
    water_elevation_m: float
    polygon: list[tuple[float, float]]   # [(lon, lat), ...]
    disclaimer: Optional[str] = None
    # 下面兩個欄位不進 to_dict()／inundation.js（前端不需要，體積也不小）：
    # 只給同一輪 build 內的 pipeline.attribution.forecast_run 用，
    # 省得它重新做一次 DEM 分析或重新猜壩頂高程。
    hypsometric_curve: Optional[list] = None   # [(el, vol_wan_m3), ...]，由低至高
    crest_el: Optional[float] = None            # 壩頂（或合成攔阻壩脊）高程；real_dem 尚未實作，為 None

    def to_dict(self) -> dict:
        d = {
            "lakeId": self.lake_id,
            "lakeName": self.lake_name,
            "method": self.method,
            "areaHa": round(self.area_ha, 2),
            "maxDepthM": round(self.max_depth_m, 1),
            "damHeightM": round(self.dam_height_m, 1),
            "volumeTargetWanM3": self.volume_at_target_wan_m3,
            "waterElevationM": round(self.water_elevation_m, 1),
            "polygon": [[lon, lat] for lon, lat in self.polygon],
        }
        if self.disclaimer:
            d["disclaimer"] = self.disclaimer
        return d


def _interp_elevation_for_volume(curve: list[tuple[float, float]],
                                  target_volume_wan_m3: float) -> float:
    """在單調遞增的水位–容積曲線上，內插出累積容積等於目標值的水位。
    目標超過曲線最大容量時，回傳曲線最高水位（並由呼叫端決定要不要
    在 disclaimer 裡註明「已超出合成地形容量上限」）。
    """
    if not curve:
        return 0.0
    if target_volume_wan_m3 <= curve[0][1]:
        return curve[0][0]
    for (el0, v0), (el1, v1) in zip(curve, curve[1:]):
        if v0 <= target_volume_wan_m3 <= v1:
            if v1 == v0:
                return el1
            frac = (target_volume_wan_m3 - v0) / (v1 - v0)
            return el0 + frac * (el1 - el0)
    return curve[-1][0]  # 超出容量：頂著曲線最高點


def _mask_to_polygon_lonlat(mask: np.ndarray, to_lonlat) -> list[tuple[float, float]]:
    """把淹沒遮罩的邊界轉成經緯度多邊形頂點（簡化過的，供前端畫圖用，
    不是精確的像元邊界幾何）。"""
    if not mask.any():
        return []
    from skimage import measure
    contours = measure.find_contours(mask.astype("float64"), level=0.5)
    if not contours:
        return []
    largest = max(contours, key=len)
    # 頂點太多前端畫起來沒意義，等距抽稀到最多 ~40 點
    step = max(1, len(largest) // 40)
    points = largest[::step]
    return [to_lonlat(r, c) for r, c in points]


def _assess_with_real_dem(lake: dict, dem_path: str) -> Optional[AssessResult]:
    """真實 DEM 路徑（目前本專案沒有實測過，因為沒有真實 DEM 檔案可用——
    邏輯已經寫好並重用 hypsometry/inundation 既有、測試過的檔案 I/O
    函式，DEM 送達後這條路徑不需要改）。"""
    lon, lat = lake["lon"], lake["lat"]
    target_volume = lake.get("volume") or 0.0

    curve = hypsometry.curve_from_dem_file(dem_path, (lon, lat))
    if not curve:
        return None
    water_el = _interp_elevation_for_volume(curve, target_volume)

    import rasterio
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float64")
        cellsize = abs(src.transform.a)
        row, col = src.index(lon, lat)
        transform = src.transform

    mask = inundation.bathtub_extent(dem, cellsize, (row, col), water_el,
                                      max_distance_km=DEFAULT_MAX_DISTANCE_KM)

    def to_lonlat(r, c):
        x, y = transform * (c, r)
        return (round(x, 6), round(y, 6))

    return AssessResult(
        lake_id=lake["id"], lake_name=lake["name"], method="real_dem",
        area_ha=inundation.extent_area_ha(mask, cellsize),
        max_depth_m=inundation.max_depth(dem, mask, water_el),
        dam_height_m=water_el - curve[0][0],
        volume_at_target_wan_m3=target_volume,
        water_elevation_m=water_el,
        polygon=_mask_to_polygon_lonlat(mask, to_lonlat),
        hypsometric_curve=curve,
        crest_el=None,  # 真實 DEM 目前沒有獨立的壩頂高程判讀，見 forecast_run.py 的說明
    )


def _assess_with_synthetic_dem(lake: dict) -> Optional[AssessResult]:
    from pipeline.assess.synthetic_dem import grow_until_capacity

    target_volume = lake.get("volume") or 0.0
    if target_volume <= 0:
        target_volume = 1.0  # 清冊登載 0（估不出容積）時，給個最小示意水位

    result, curve = grow_until_capacity(
        lake["id"], lake["lat"], lake["lon"], target_volume)
    water_el = _interp_elevation_for_volume(curve, target_volume)
    dam_height_m = water_el - curve[0][0]

    capacity_note = ""
    if target_volume > curve[-1][1]:
        capacity_note = ("；官方登載蓄水量超出合成地形容量上限，"
                          "本結果已頂著合成地形能容納的最大水位")

    # 下游淹沒範圍：架構文件的 bathtub model 是「假設瞬時全潰」的保守快估，
    # 明確不含流量衰減（見 inundation.py 檔頭），所以下游水位不該套壩前的
    # 絕對高程（那是「這座山谷本身能蓄多高」，跟下游地形是兩個不同的
    # 高程基準，直接套用會得到離譜的水深——這是曾經有過、已修正的 bug）。
    # 改用「出水點的下游地形高程 + 壩高」當下游模擬水位：意義是
    # 「整個水頭瞬間出現在出水點」，跟真正的下游衰減無關，
    # 由既有的 max_distance_km（2 km）負責限制不合理的無限蔓延，
    # 這正是 inundation.py 原本就採用的保守設計。
    downstream_pour_el = float(result.dem[result.downstream_pour_rc])
    water_el_downstream = downstream_pour_el + dam_height_m

    downstream_mask = inundation.bathtub_extent(
        result.dem, result.cellsize, result.downstream_pour_rc, water_el_downstream,
        max_distance_km=DEFAULT_MAX_DISTANCE_KM)

    polygon = _mask_to_polygon_lonlat(downstream_mask, result.rowcol_to_lonlat)

    return AssessResult(
        lake_id=lake["id"], lake_name=lake["name"], method="synthetic_demo_dem",
        area_ha=inundation.extent_area_ha(downstream_mask, result.cellsize),
        max_depth_m=inundation.max_depth(result.dem, downstream_mask, water_el_downstream),
        dam_height_m=dam_height_m,
        volume_at_target_wan_m3=target_volume,
        water_elevation_m=water_el,
        polygon=polygon,
        disclaimer=(
            "地形為合成示範資料（山谷+攔阻壩幾何頂著，蓄水量已校準至清冊"
            "登載數字，但谷寬/坡度/壩高等其餘地形參數皆非本湖真實測量值），"
            "尚未接上真實 DEM，此淹沒範圍僅供流程展示，不可作為實際避難"
            "疏散依據" + capacity_note
        ),
        hypsometric_curve=curve,
        crest_el=result.ridge_crest_el,
    )


def assess_lake(lake: dict, dem_dir: str = DEFAULT_DEM_DIR,
                 allow_synthetic: bool = False) -> Optional[AssessResult]:
    """給一筆湖泊紀錄（來自 lakes.js 解析出的 dict），回傳淹沒評估結果；
    沒有真實 DEM 且 `allow_synthetic=False` 時回傳 None（呼叫端應該
    印出「跳過」而不是讓整條 pipeline 失敗——沿用本專案一貫的降級原則）。
    """
    lake_id = lake.get("id")
    dem_path = os.path.join(dem_dir, f"{lake_id}.tif")
    if os.path.exists(dem_path):
        return _assess_with_real_dem(lake, dem_path)

    taiwan_dem_path = os.path.join(dem_dir, "taiwan_dem.tif")
    if os.path.exists(taiwan_dem_path):
        return _assess_with_real_dem(lake, taiwan_dem_path)

    if allow_synthetic:
        return _assess_with_synthetic_dem(lake)

    return None


def assess_watch_lakes(lakes: list[dict], dem_dir: str = DEFAULT_DEM_DIR,
                        allow_synthetic: bool = False) -> list[AssessResult]:
    """只處理 statusKey == 'watch' 的湖泊（理由見檔頭）。"""
    results = []
    for lake in lakes:
        if lake.get("statusKey") != "watch":
            continue
        r = assess_lake(lake, dem_dir=dem_dir, allow_synthetic=allow_synthetic)
        if r is not None:
            results.append(r)
        else:
            print(f"  ⚠ {lake.get('name')}：無真實 DEM 且未開啟 --demo-dem，跳過")
    return results


def write_inundation_js(path: str, results: list[AssessResult]) -> None:
    body = json.dumps({r.lake_id: r.to_dict() for r in results},
                       ensure_ascii=False, indent=1)
    has_synthetic = any(r.method == "synthetic_demo_dem" for r in results)
    if not results:
        header_note = "   assess 步驟未執行（沒有真實 DEM，且 build_all 未加 --demo-dem），內容為空。\n"
    elif has_synthetic:
        header_note = (
            "   部分或全部結果 method 為 synthetic_demo_dem——地形是合成示範資料，\n"
            "   不是真實 DEM，前端（app.js/cap.js）已會顯示 disclaimer 欄位，\n"
            "   不可當成真實地形分析結果。\n"
        )
    else:
        header_note = "   全部結果皆來自真實 DEM。\n"
    out = (
        "/* 由 pipeline/assess/run.py 產生，請勿手動編輯。\n"
        "   只包含清冊 statusKey == 'watch' 的湖泊。\n"
        f"{header_note}"
        "   dashboard/cap.js 會優先用本檔的 polygon 取代固定半徑的 circle。 */\n\n"
        f"window.LAKE_INUNDATION = {body};\n"
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
