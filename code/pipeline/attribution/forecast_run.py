#!/usr/bin/env python3
"""
forecast_run.py — 把 assess 算出的水位–容積曲線接上 forecast.py 的水量平衡預報

背景（架構文件 §03「已知限制」／README「決定 CAP 的風險依據」）：
    `dashboard/cap.js` 的 severity/urgency/certainty 一直是用
    `pipeline.ingest.risk`（ERA5-Land + 邏輯迴歸）算的，這件事不變——
    它是目前唯一能自動覆蓋清冊裡幾乎所有湖泊的信號，儘管正樣本少、
    沒有留出驗證（已在 certainty 誠實反映為 "Possible"）。

    `pipeline.attribution.forecast`（水量平衡、區間輸出、方法可稽核）
    做的是不同的事：不是「這座湖危不危險」的分級，而是「已經被判定
    該關注的湖，大概還有多久可能滿水位溢流」的時間預估。這兩者分工
    不同，不是二選一——本檔負責把它們接在一起展示，但不讓 forecast
    的結果覆蓋或混進 severity/urgency 的判斷。

    forecast.py 需要集水面積（catchment_km2），這是本專案完全没有
    自動算出來的東西（真實集水區向量圖資尚未整合，見
    `pipeline/trigger/catchment.py` 的 TODO）。與其用合成地形硬湊一個
    看起來像真的數字，這裡選擇誠實地只在「查得到公開可信來源」時才
    計算預報，其餘湖泊一律回傳 None（前端顯示「集水面積未知，無法
    估算溢流時間」，不是拿假數字充數）。

目前只有花蓮馬太鞍溪（bl071）有這樣的來源：農業部（農村水保署／
林業及自然保育署）資料顯示「壩體上游集水區面積 6,323 公頃」，見
維基百科「花蓮馬太鞍溪堰塞湖災害」條目引述（下面 CITATION 常數）。
壩頂高程等其餘參數則沿用本次合成地形（`synthetic_dem.py`）算出的
crest_el／hypsometric_curve，因此預報的「時間」本身仍然是示範性質
（合成地形），只有集水面積這一個輸入是查證過的真實數字——這個區分
必須誠實反映在輸出的 disclaimer 裡，不能讓一個真數字讓整包結果看起來
比實際上更可信。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pipeline.attribution import forecast as F

CITATION = (
    "農業部（農村水保署／林業及自然保育署）資料：壩體上游集水區面積 6,323 公頃，"
    "轉引自維基百科「花蓮馬太鞍溪堰塞湖災害」條目（存取日期為本專案開發期間）"
)

# 只收錄「查得到公開可信來源」的集水面積，其餘湖泊一律不猜。
# key 用清冊 id，跟 assess/run.py、trigger/catchment.py 用同一套 id 慣例。
KNOWN_CATCHMENTS_KM2: dict[str, dict] = {
    "bl071": {"area_km2": 63.23, "source": CITATION},
}

DEFAULT_RUNOFF_COEFFICIENT = 0.65  # forecast.BasinParams 預設值，震後裸露地應調高


@dataclass
class LakeForecastResult:
    lake_id: str
    lake_name: str
    catchment_km2: float
    catchment_source: str
    gap_m: float
    remaining_wan_m3: float
    earliest: Optional[datetime]
    median: Optional[datetime]
    latest: Optional[datetime]
    any_overflow: bool
    narrative: list[str]        # 由 forecast.describe_forecast 產生的中文敘述
    disclaimer: str

    def to_dict(self) -> dict:
        def iso(t):
            return t.isoformat() if t else None
        return {
            "lakeId": self.lake_id,
            "lakeName": self.lake_name,
            "catchmentKm2": self.catchment_km2,
            "catchmentSource": self.catchment_source,
            "gapM": round(self.gap_m, 1),
            "remainingWanM3": round(self.remaining_wan_m3, 1),
            "earliest": iso(self.earliest),
            "median": iso(self.median),
            "latest": iso(self.latest),
            "anyOverflow": self.any_overflow,
            "narrative": self.narrative,
            "disclaimer": self.disclaimer,
        }


def _load_templates() -> dict:
    import pathlib
    import yaml
    path = pathlib.Path(__file__).parent / "templates.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def forecast_for_assess_result(lake: dict, assess_result, observed_at: Optional[datetime] = None
                                ) -> Optional[LakeForecastResult]:
    """給一筆清冊湖泊紀錄 + 對應的 `pipeline.assess.run.AssessResult`，
    在集水面積已知時算出溢流預報；否則回傳 None（呼叫端印出「跳過」，
    不當成錯誤）。

    只吃 assess_result.hypsometric_curve / crest_el 都不是 None 的情況——
    real_dem 路徑目前 crest_el 一律是 None（見 assess/run.py 註解），
    所以現階段等同「只有 synthetic_demo_dem 且集水面積已知時才會有結果」，
    這是誠實的現況，不是本檔的判斷疏漏。
    """
    lake_id = lake.get("id")
    catchment = KNOWN_CATCHMENTS_KM2.get(lake_id)
    if catchment is None:
        return None
    if assess_result is None or assess_result.hypsometric_curve is None \
            or assess_result.crest_el is None:
        return None

    curve = assess_result.hypsometric_curve
    crest_el = assess_result.crest_el
    water_el = assess_result.water_elevation_m
    floor_el = curve[0][0]
    observed_at = observed_at or datetime.utcnow()

    state = F.LakeState(
        water_el=water_el, crest_el=crest_el, floor_el=floor_el,
        hypsometric=curve, observed_at=observed_at,
    )
    params = F.BasinParams(
        catchment_km2=catchment["area_km2"],
        runoff_coefficient=DEFAULT_RUNOFF_COEFFICIENT,
    )
    lake_area_km2 = (assess_result.area_ha or 0.0) / 100.0

    fc = F.forecast(state, params, lake_area_km2=lake_area_km2)

    templates = _load_templates()
    narrative = [sentence for sentence, _rule in F.describe_forecast(fc, templates)]

    disclaimer_parts = [
        f"集水面積 {catchment['area_km2']:g} km² 為查證過的真實數字（來源：{catchment['source']}），"
        f"但水位–容積曲線與壩頂高程仍沿用本次合成示範地形（見 dashboard/data/inundation.js 的 "
        f"disclaimer），因此「溢流時間」本身仍是示範性質，不可作為實際應變依據。",
    ]
    if assess_result.method == "synthetic_demo_dem":
        disclaimer_parts.append(
            "此結果僅示範 forecast.py 的水量平衡方法本身可用、可稽核，"
            "不代表本系統目前已能對這座湖做出可上線使用的真實溢流時間預測。"
        )

    return LakeForecastResult(
        lake_id=lake_id, lake_name=lake.get("name", ""),
        catchment_km2=catchment["area_km2"], catchment_source=catchment["source"],
        gap_m=fc.gap_m, remaining_wan_m3=fc.remaining_wan_m3,
        earliest=fc.earliest, median=fc.median, latest=fc.latest,
        any_overflow=fc.any_overflow, narrative=narrative,
        disclaimer="".join(disclaimer_parts),
    )


def forecast_all(lakes: list[dict], assess_results: dict,
                  observed_at: Optional[datetime] = None) -> list[LakeForecastResult]:
    """`assess_results`：{lake_id: AssessResult}，通常直接用
    `{r.lake_id: r for r in assess_watch_lakes(...)}` 建。"""
    out = []
    for lake in lakes:
        r = assess_results.get(lake.get("id"))
        fc = forecast_for_assess_result(lake, r, observed_at=observed_at)
        if fc is not None:
            out.append(fc)
    return out


def write_forecast_js(path: str, results: list[LakeForecastResult]) -> None:
    import json
    import os

    body = json.dumps({r.lake_id: r.to_dict() for r in results}, ensure_ascii=False, indent=1)
    out = (
        "/* 由 pipeline/attribution/forecast_run.py 產生，請勿手動編輯。\n"
        "   只包含「查得到公開可信集水面積來源」且已跑過 assess 的湖泊——\n"
        "   目前只有花蓮馬太鞍溪（bl071），其餘湖泊沒有捏造的集水面積，\n"
        "   因此不會出現在這裡（前端應顯示「集水面積未知，無法估算溢流時間」，\n"
        "   不是當成錯誤處理）。\n"
        "   跟 dashboard/data/risk.js（CAP severity/urgency 的依據）是分開的\n"
        "   兩件事：risk.js 負責「這座湖危不危險」的分級，本檔負責「已經被\n"
        "   判定該關注的湖，大概還有多久可能溢流」的時間預估區間。 */\n\n"
        f"window.LAKE_FORECAST = {body};\n"
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
