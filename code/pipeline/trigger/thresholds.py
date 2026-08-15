#!/usr/bin/env python3
"""
thresholds.py — 觸發層核心規則（架構文件 §04「觸發規則 v1」）

    地震觸發:  規模 ≥ 5.5 且任一山區測站震度 ≥ 5弱
               → 圈定震央 30 km 內、坡度 > 30° 的集水區
    豪雨觸發:  山區時雨量 ≥ 80 mm 或 24h 累積 ≥ 350 mm
               → 圈定達標雨量站所屬集水區
    複合加權:  地震後 30 天內遇豪雨 → 門檻下修 20%
               （震後坡體鬆動，正是馬太鞍溪型事件的成因鏈）

本檔刻意只做「判斷」，不做任何網路或檔案 I/O——沿用本專案
`pipeline.attribution.rules` 的階段劃分原則：判斷邏輯要能離線單元測試，
資料怎麼抓是另一層的事（見 pipeline.trigger.earthquake / pipeline.ingest.cwa）。

已知簡化（誠實列出，之後要補的地方）：
    · 「山區測站」判斷目前只用經緯度是否落在概略山地範圍，
      不是真正查測站海拔——CWA 地震測站資料通常沒有附高程欄位。
      若之後拿得到測站海拔，把 EarthquakeStation.elevation_m 填上，
      is_mountain_station() 會自動改用海拔判斷（見函式內註解）。
    · 坡度 > 30° 的篩選不在本檔——這一步需要 DEM，
      留給 pipeline.trigger.catchment 在有 DEM 時才做（見該檔案 TODO）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

# ── 門檻常數（唯一該調整規則的地方，回測後在這裡校準）──────────

EARTHQUAKE_MAGNITUDE_THRESHOLD = 5.5
EARTHQUAKE_MIN_INTENSITY = "5弱"      # 任一測站達到此震度即觸發
EARTHQUAKE_CATCHMENT_RADIUS_KM = 30.0
EARTHQUAKE_SLOPE_THRESHOLD_DEG = 30.0  # 交給 catchment.py 使用

RAIN_HOURLY_THRESHOLD_MM = 80.0
RAIN_24H_THRESHOLD_MM = 350.0

COMPOUND_WINDOW_DAYS = 30
COMPOUND_DISCOUNT = 0.20               # 門檻下修 20%

# 概略山地範圍（台灣本島 + 外島山區的寬鬆 bounding box，僅供
# is_mountain_station() 的「粗篩」用；不是精確的地形分類，
# 真正該用坡度圖層時已在 catchment.py 留了介面）。
_TAIWAN_MOUNTAIN_BBOX = dict(lat_min=21.8, lat_max=25.4, lon_min=120.0, lon_max=122.1)

# CWA 中央氣象署震度分級（由低到高），用來比較「≥ 某震度」。
INTENSITY_SCALE = [
    "0", "1", "2", "3", "4", "5弱", "5強", "6弱", "6強", "7",
]
_INTENSITY_RANK = {label: i for i, label in enumerate(INTENSITY_SCALE)}


def intensity_rank(label: str) -> int:
    """把震度標籤轉成可比較大小的整數等級；無法辨識時視為最低等級。

    >>> intensity_rank("5弱") > intensity_rank("4")
    True
    >>> intensity_rank("6強") > intensity_rank("5弱")
    True
    """
    label = (label or "").strip()
    if label in _INTENSITY_RANK:
        return _INTENSITY_RANK[label]
    # 舊制資料偶爾只給數字（如 "5"），沒有弱/強細分，視同該級的「弱」。
    try:
        n = int(float(label))
    except (TypeError, ValueError):
        return -1
    candidate = f"{n}弱" if f"{n}弱" in _INTENSITY_RANK else str(n)
    return _INTENSITY_RANK.get(candidate, -1)


def intensity_at_least(label: str, threshold: str = EARTHQUAKE_MIN_INTENSITY) -> bool:
    """label 的震度是否達到 threshold（含）。

    >>> intensity_at_least("5弱")
    True
    >>> intensity_at_least("4")
    False
    """
    return intensity_rank(label) >= intensity_rank(threshold)


# ── 資料結構 ─────────────────────────────────────────────

@dataclass
class EarthquakeStation:
    name: str
    lat: float
    lon: float
    intensity: str              # CWA 震度標籤，如 "5弱"
    elevation_m: Optional[float] = None


@dataclass
class EarthquakeObservation:
    """一筆地震報告（來自 pipeline.trigger.earthquake.fetch_significant_earthquakes）。"""
    time: datetime
    magnitude: float
    epicenter_lat: float
    epicenter_lon: float
    stations: list[EarthquakeStation] = field(default_factory=list)
    source_id: Optional[str] = None   # CWA 報告編號，供稽核追溯


@dataclass
class RainfallStation:
    name: str
    lat: float
    lon: float
    rain_1h_mm: Optional[float] = None
    rain_24h_mm: Optional[float] = None


def is_mountain_station(lat: float, lon: float, elevation_m: Optional[float] = None) -> bool:
    """粗略判斷測站是否屬於「山區」。

    有海拔資料時用海拔（> 100 m 視為山區，跟 preprocess/mask.py
    坡度篩選的精神一致，只是這裡沒有 DEM 只能先用海拔近似）；
    沒有海拔資料則退回「落在台灣山地概略範圍內」的寬鬆判斷，
    寧可誤納（觸發過度保守）也不要誤刪（漏掉真正的山區事件）。
    """
    if elevation_m is not None:
        return elevation_m > 100.0
    b = _TAIWAN_MOUNTAIN_BBOX
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]


# ── 地震觸發 ─────────────────────────────────────────────

@dataclass
class TriggerDecision:
    triggered: bool
    trigger_type: str            # "quake" | "rain" | "compound"
    reasons: list[str]
    discount_applied: float = 0.0


def quake_exceeds_threshold(quake: EarthquakeObservation) -> TriggerDecision:
    """判斷一筆地震報告是否達到觸發門檻。

    >>> from datetime import datetime
    >>> st = [EarthquakeStation("光復", 23.67, 121.42, "5弱")]
    >>> q = EarthquakeObservation(datetime(2025, 9, 1), 6.2, 23.7, 121.4, st)
    >>> quake_exceeds_threshold(q).triggered
    True
    """
    reasons = []
    mag_ok = quake.magnitude >= EARTHQUAKE_MAGNITUDE_THRESHOLD
    reasons.append(
        f"規模 {quake.magnitude} {'≥' if mag_ok else '<'} "
        f"{EARTHQUAKE_MAGNITUDE_THRESHOLD}"
    )

    hit_stations = [
        s for s in quake.stations
        if is_mountain_station(s.lat, s.lon, s.elevation_m)
        and intensity_at_least(s.intensity)
    ]
    intensity_ok = len(hit_stations) > 0
    if intensity_ok:
        names = "、".join(s.name for s in hit_stations[:3])
        reasons.append(f"山區測站達 {EARTHQUAKE_MIN_INTENSITY} 以上：{names}")
    else:
        reasons.append(f"無山區測站達 {EARTHQUAKE_MIN_INTENSITY}")

    return TriggerDecision(
        triggered=mag_ok and intensity_ok,
        trigger_type="quake",
        reasons=reasons,
    )


# ── 豪雨觸發（含複合加權）───────────────────────────────

def compound_discount(quake_time: Optional[datetime], rain_time: datetime,
                       window_days: int = COMPOUND_WINDOW_DAYS,
                       discount: float = COMPOUND_DISCOUNT) -> float:
    """地震後 window_days 天內遇豪雨 → 回傳門檻下修比例；否則回傳 0。

    >>> from datetime import datetime
    >>> compound_discount(datetime(2025, 9, 1), datetime(2025, 9, 10))
    0.2
    >>> compound_discount(datetime(2025, 9, 1), datetime(2025, 11, 1))
    0.0
    >>> compound_discount(None, datetime(2025, 9, 10))
    0.0
    """
    if quake_time is None:
        return 0.0
    if timedelta(0) <= (rain_time - quake_time) <= timedelta(days=window_days):
        return discount
    return 0.0


def rain_exceeds_threshold(station: RainfallStation, rain_time: datetime,
                            last_significant_quake_time: Optional[datetime] = None
                            ) -> TriggerDecision:
    """判斷一個雨量站觀測是否達到觸發門檻，並套用複合加權。

    >>> from datetime import datetime
    >>> s = RainfallStation("崙天", 23.5, 121.3, rain_1h_mm=85.0, rain_24h_mm=120.0)
    >>> rain_exceeds_threshold(s, datetime(2025, 9, 1)).triggered
    True
    """
    disc = compound_discount(last_significant_quake_time, rain_time)
    hourly_th = RAIN_HOURLY_THRESHOLD_MM * (1 - disc)
    daily_th = RAIN_24H_THRESHOLD_MM * (1 - disc)

    r1h = station.rain_1h_mm or 0.0
    r24h = station.rain_24h_mm or 0.0
    hourly_ok = r1h >= hourly_th
    daily_ok = r24h >= daily_th

    reasons = [
        f"時雨量 {r1h:.1f}mm {'≥' if hourly_ok else '<'} {hourly_th:.1f}mm"
        + ("（已套複合加權 -20%）" if disc else ""),
        f"24h累積 {r24h:.1f}mm {'≥' if daily_ok else '<'} {daily_th:.1f}mm"
        + ("（已套複合加權 -20%）" if disc else ""),
    ]

    return TriggerDecision(
        triggered=hourly_ok or daily_ok,
        trigger_type="compound" if disc else "rain",
        reasons=reasons,
        discount_applied=disc,
    )


if __name__ == "__main__":
    import doctest
    n_fail, n_run = doctest.testmod()
    print(f"doctest: {n_run - n_fail}/{n_run} passed")
