#!/usr/bin/env python3
"""
forecast.py — 溢流預報（水量平衡）

核心式子
--------
    剩餘容量 = V(壩頂高程) − V(當前水位)
    淨入流   = 降雨 × 集水面積 × 逕流係數 − 滲流 − 蒸發
    溢流時間 = 剩餘容量 / 淨入流

三個工程紀律
------------
1. **一律輸出區間，不輸出單一時間點。** 用高／中／低三個雨量情境
   各算一次，回傳最早、中位、最晚。災防上假精確比不精確更危險。

2. **逕流係數是最大不確定來源。** 震後裸露坡面可從 0.5 跳到 0.8 以上，
   同樣的雨進來的水差很多。此參數必須外顯、可調、並在輸出中揭露。

3. **兩次衛星過境間為純外推。** Sentinel-1 約 6 日重訪，期間水位靠雨量
   推估、沒有實測校正。這件事必須隨每次輸出一起講清楚。

本模組不含機器學習，全部是確定性計算，可寫單元測試。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from . import verbalize as V


# ══════════════════════════════════════════
# 參數
# ══════════════════════════════════════════

@dataclass
class BasinParams:
    """集水區與壩體參數。除面積外皆有預設值，但都應逐案校正。"""
    catchment_km2: float                  # 集水面積
    runoff_coefficient: float = 0.65      # 逕流係數（震後裸露地應調高）
    seepage_cms: float = 0.0              # 壩體滲流量 m³/s
    evaporation_mm_day: float = 3.0       # 蒸發量

    def note(self) -> str:
        return (f"集水面積 {self.catchment_km2:g} km²、"
                f"逕流係數 {self.runoff_coefficient:.2f}、"
                f"滲流 {self.seepage_cms:g} cms")


@dataclass
class RainScenario:
    """一個降雨情境。mm_per_hour 為預報時段內的平均時雨量。"""
    name: str
    mm_per_hour: float
    hours: int = 72                       # 此情境的有效預報時距


@dataclass
class LakeState:
    """當前湖體狀態。高程單位為公尺，容積為萬立方公尺。"""
    water_el: float
    crest_el: float
    floor_el: float
    hypsometric: list                     # [(高程, 累積容積萬m3), ...] 由低至高
    observed_at: datetime                 # 此狀態依據的影像時刻

    @property
    def gap_m(self) -> float:
        return self.crest_el - self.water_el


@dataclass
class ScenarioResult:
    scenario: str
    inflow_cms: float
    net_inflow_cms: float
    overflow_at: Optional[datetime]
    hours_to_overflow: Optional[float]


@dataclass
class Forecast:
    gap_m: float
    remaining_wan_m3: float
    scenarios: list = field(default_factory=list)
    earliest: Optional[datetime] = None
    median: Optional[datetime] = None
    latest: Optional[datetime] = None
    basis_time: Optional[datetime] = None
    staleness: str = "stale"
    assumptions: str = ""

    @property
    def any_overflow(self) -> bool:
        return any(s.overflow_at for s in self.scenarios)


# ══════════════════════════════════════════
# 水位–容積曲線
# ══════════════════════════════════════════

def volume_at(hypsometric: list, elevation: float) -> float:
    """
    查水位–容積曲線，線性內插。單位：萬立方公尺。

    曲線由 DEM 對壩址上游做填洼分析一次性建立，
    之後每次量到水面範圍就反查，不必重算地形。

    >>> curve = [(640, 0), (660, 300), (680, 1000), (700, 2200)]
    >>> volume_at(curve, 640)
    0.0
    >>> volume_at(curve, 670)
    650.0
    >>> volume_at(curve, 700)
    2200.0
    """
    els = [e for e, _ in hypsometric]
    vols = [v for _, v in hypsometric]

    if elevation <= els[0]:
        return float(vols[0])
    if elevation >= els[-1]:
        return float(vols[-1])

    i = bisect.bisect_right(els, elevation) - 1
    e0, e1 = els[i], els[i + 1]
    v0, v1 = vols[i], vols[i + 1]
    t = (elevation - e0) / (e1 - e0)
    return float(v0 + t * (v1 - v0))


def elevation_at(hypsometric: list, volume: float) -> float:
    """
    反查：給定蓄水量求水位。用於由蓄水量推估目前水位。

    >>> curve = [(640, 0), (660, 300), (680, 1000), (700, 2200)]
    >>> elevation_at(curve, 650.0)
    670.0
    """
    els = [e for e, _ in hypsometric]
    vols = [v for _, v in hypsometric]

    if volume <= vols[0]:
        return float(els[0])
    if volume >= vols[-1]:
        return float(els[-1])

    i = bisect.bisect_right(vols, volume) - 1
    v0, v1 = vols[i], vols[i + 1]
    e0, e1 = els[i], els[i + 1]
    t = (volume - v0) / (v1 - v0)
    return float(e0 + t * (e1 - e0))


def remaining_capacity(state: LakeState) -> float:
    """
    剩餘容量（萬立方公尺）＝ 壩頂容積 − 當前容積。

    >>> curve = [(640, 0), (660, 300), (680, 1000)]
    >>> st = LakeState(670, 680, 640, curve, datetime(2026,7,23,6,12))
    >>> remaining_capacity(st)
    350.0
    """
    return volume_at(state.hypsometric, state.crest_el) - \
           volume_at(state.hypsometric, state.water_el)


# ══════════════════════════════════════════
# 入流量
# ══════════════════════════════════════════

def inflow_cms(rain_mm_per_hour: float, params: BasinParams) -> float:
    """
    合理化公式（Rational Method）：Q = C · i · A

    單位換算：mm/hr × km² = 1000 m³/hr = 0.2778 m³/s

    >>> p = BasinParams(catchment_km2=42.6, runoff_coefficient=0.65)
    >>> round(inflow_cms(20.0, p), 1)
    153.8
    """
    return params.runoff_coefficient * rain_mm_per_hour * params.catchment_km2 * 0.2778


def net_inflow_cms(rain_mm_per_hour: float, params: BasinParams,
                   lake_area_km2: float = 0.0) -> float:
    """
    淨入流 ＝ 逕流 − 滲流 − 蒸發。蒸發量小，但在低雨量情境下不可忽略。

    >>> p = BasinParams(catchment_km2=42.6, seepage_cms=5.0)
    >>> round(net_inflow_cms(20.0, p), 1)
    148.8
    """
    evap_cms = (params.evaporation_mm_day / 1000 / 86400) * lake_area_km2 * 1e6
    return inflow_cms(rain_mm_per_hour, params) - params.seepage_cms - evap_cms


# ══════════════════════════════════════════
# 預報
# ══════════════════════════════════════════

DEFAULT_SCENARIOS = [
    RainScenario("低情境", mm_per_hour=2.0),
    RainScenario("中情境", mm_per_hour=8.0),
    RainScenario("高情境", mm_per_hour=20.0),
]


def forecast(state: LakeState,
             params: BasinParams,
             scenarios: Optional[list] = None,
             now: Optional[datetime] = None,
             lake_area_km2: float = 0.0) -> Forecast:
    """
    對每個雨量情境算一次溢流時間，回傳區間。

    情境的雨量應由 CWA 的 QPF（定量降水預報）帶入，
    而非 QPE（觀測估計）——預報與現況分析用的是不同產品。
    """
    scenarios = scenarios or DEFAULT_SCENARIOS
    now = now or state.observed_at

    remaining = remaining_capacity(state)          # 萬 m³
    remaining_m3 = remaining * 1e4

    results = []
    for sc in scenarios:
        net = net_inflow_cms(sc.mm_per_hour, params, lake_area_km2)
        if net <= 0:
            results.append(ScenarioResult(sc.name, inflow_cms(sc.mm_per_hour, params),
                                          net, None, None))
            continue

        hours = remaining_m3 / (net * 3600)
        if hours > sc.hours:
            # 超出該情境的預報時距，不外推——寧可說「時距內不致溢流」
            results.append(ScenarioResult(sc.name, inflow_cms(sc.mm_per_hour, params),
                                          net, None, None))
            continue

        results.append(ScenarioResult(
            sc.name,
            inflow_cms(sc.mm_per_hour, params),
            net,
            now + timedelta(hours=hours),
            hours,
        ))

    times = sorted(r.overflow_at for r in results if r.overflow_at)
    median = times[len(times) // 2] if times else None

    return Forecast(
        gap_m=state.gap_m,
        remaining_wan_m3=remaining,
        scenarios=results,
        earliest=times[0] if times else None,
        median=median,
        latest=times[-1] if times else None,
        basis_time=state.observed_at,
        staleness=V.staleness_key(now - state.observed_at),
        assumptions=params.note(),
    )


# ══════════════════════════════════════════
# 敘述（沿用 templates.yaml 的 forecast 區段）
# ══════════════════════════════════════════

def describe_forecast(fc: Forecast, templates: dict,
                      next_pass_hours: Optional[float] = None,
                      horizon_hours: int = 72) -> list:
    """
    把預報結果轉成句子清單。與 compose.py 同樣採填槽法，
    缺值就整句略過。回傳 (句子, 規則名) 的清單供稽核。
    """
    T = templates.get("forecast", {})
    out = []

    head = T.get("headline", {}).get("text")
    if head:
        out.append((head.format(
            basis_time=V.timepoint(fc.basis_time),
            gap=V.elevation_gap(fc.gap_m)), "forecast.headline"))

    if fc.any_overflow and fc.earliest and fc.latest and fc.median:
        if fc.earliest == fc.latest:
            tpl = T.get("window", {}).get("single")
            if tpl:
                out.append((tpl.format(median=V.timepoint(fc.median)),
                            "forecast.window.single"))
        else:
            tpl = T.get("window", {}).get("text")
            if tpl:
                out.append((tpl.format(
                    earliest=V.timepoint(fc.earliest),
                    latest=V.timepoint(fc.latest),
                    median=V.timepoint(fc.median)), "forecast.window.text"))
    else:
        tpl = T.get("window", {}).get("no_overflow")
        if tpl:
            out.append((tpl.format(horizon=f"{horizon_hours} 小時"),
                        "forecast.window.no_overflow"))

    stale_tpl = T.get("staleness", {}).get(fc.staleness)
    if stale_tpl:
        age_text = V.age(datetime.now() - fc.basis_time) if fc.basis_time else None
        slots = {"age": age_text,
                 "next_pass": f"{next_pass_hours:.0f} 小時" if next_pass_hours else None}
        if all(slots.get(k) is not None for k in
               [m for m in ("age", "next_pass") if "{" + m + "}" in stale_tpl]):
            out.append((stale_tpl.format(**{k: v for k, v in slots.items() if v}),
                        f"forecast.staleness.{fc.staleness}"))

    disc = T.get("disclaimer", {}).get("text")
    if disc:
        out.append((disc, "forecast.disclaimer"))

    return out


if __name__ == "__main__":
    # 執行方式：python -m pipeline.attribution.forecast
    import doctest
    fails, total = doctest.testmod()
    print(f"forecast: {total - fails}/{total} 通過")

    # 示範：馬太鞍溪型的量級
    curve = [(640, 0), (650, 180), (660, 900), (670, 3200),
             (676, 6800), (682, 9100)]
    state = LakeState(water_el=675.6, crest_el=682.0, floor_el=640.0,
                      hypsometric=curve,
                      observed_at=datetime(2026, 7, 23, 6, 12))
    params = BasinParams(catchment_km2=42.6, runoff_coefficient=0.72,
                         seepage_cms=8.0)

    fc = forecast(state, params, lake_area_km2=1.2)
    print(f"\n距壩頂 {fc.gap_m:.1f} m，剩餘容量 {fc.remaining_wan_m3:,.0f} 萬 m³")
    for s in fc.scenarios:
        when = V.timepoint(s.overflow_at) or "時距內不致溢流"
        print(f"  {s.scenario}：淨入流 {s.net_inflow_cms:7.1f} cms → {when}")
