#!/usr/bin/env python3
"""
rules.py — 結構化歸因判定

輸入一筆堰塞湖紀錄（可選附上氣象／地震觀測），輸出一組
「命中的規則」與「填槽用的欄位值」。這一層只做判斷與計算，
不產生任何文字——文字交給 compose.py 依 templates.yaml 組裝。

為何分層
--------
判定與敘述分離，才能做到：
  · 每句話都追得到是哪條規則、哪個門檻造成的
  · 門檻調整時不必動到句型，句型修改時不必動到邏輯
  · 規則可單獨寫單元測試

門檻來源
--------
雨量分級   中央氣象署定義（見 verbalize.RAIN_GRADES_24H）
颱風距離   本專案自訂，依中心距離區分直接侵襲／外圍環流／西南氣流
地震關聯   形成時間在地震後 72 小時內，且規模 ≥ 5.0
以上自訂門檻皆應於對外簡報中註明為專案假設。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from . import verbalize as V


# ══════════════════════════════════════════
# 資料結構
# ══════════════════════════════════════════

@dataclass
class LakeRecord:
    """清冊的一筆紀錄。欄位對應 data/lakes.js。"""
    seq: int
    name: str
    year: Optional[int] = None
    county: str = ""
    town: str = ""
    village: str = ""
    landmark: str = ""
    cause: str = ""              # 誘因原文，可能同時含多個
    event: str = ""              # 事件名稱，如「薇帕颱風」「九二一地震」
    formed: str = ""             # 形成時間原文
    duration: str = ""           # 持續時間原文（可能是數字或文字）
    volume: Optional[float] = None   # 萬立方公尺
    breach_date: str = ""
    breach_cause: str = ""
    status: str = ""
    setting: str = ""
    lon: Optional[float] = None
    lat: Optional[float] = None
    dam_xy: Optional[tuple] = None    # TWD97 壩體座標
    slide_xy: Optional[tuple] = None  # TWD97 崩塌地座標
    river: str = ""              # 若清冊未載，由名稱推得


@dataclass
class Observations:
    """
    形成期間的觀測資料。全部為選填——沒有就不輸出對應句子，
    絕不以推測填補。
    """
    rain_24h_mm: Optional[float] = None
    rain_window_hours: int = 24
    rain_max_hourly_mm: Optional[float] = None
    rain_percentile: Optional[float] = None      # 該站歷史百分位 0–100
    typhoon_name: Optional[str] = None
    typhoon_distance_km: Optional[float] = None
    southwest_flow: Optional[bool] = None        # 西南風場是否顯著
    quake_name: Optional[str] = None
    quake_time: Optional[datetime] = None
    quake_magnitude: Optional[float] = None
    pga_gal: Optional[float] = None
    formed_time: Optional[datetime] = None
    frontal_system: Optional[bool] = None        # 梅雨鋒面滯留

    def has_any(self) -> bool:
        """是否帶有任何觀測值。全空代表尚未介接資料源，
        此時不應輸出「雨量資料不足」——那會讓每筆敘述都掛一句雜訊。"""
        # rain_window_hours 有非 None 的預設值，不算作「查過觀測」
        return any(v is not None for k, v in vars(self).items()
                   if k != "rain_window_hours")


@dataclass
class Attribution:
    """判定結果。slots 供模板填槽，rules_fired 供稽核。"""
    rules_fired: list = field(default_factory=list)
    slots: dict = field(default_factory=dict)

    def fire(self, rule: str, **slots):
        self.rules_fired.append(rule)
        self.slots.update({k: v for k, v in slots.items() if v is not None})


# ══════════════════════════════════════════
# 門檻常數（集中管理，便於稽核與調整）
# ══════════════════════════════════════════

TYPHOON_DIRECT_KM = 100        # 中心距離小於此值視為直接侵襲
TYPHOON_OUTER_KM = 300         # 100–300 km 視為外圍環流
QUAKE_WINDOW_HOURS = 72        # 地震後多久內形成視為相關
QUAKE_MIN_MAGNITUDE = 5.0
PGA_REPORT_THRESHOLD = 25.0    # 低於此值不敘述地表加速度（意義有限）

# 清冊名稱常冠縣市前綴（如「花蓮馬太鞍溪」），推河川名前先剝除
COUNTY_PREFIXES = (
    "臺北", "台北", "新北", "桃園", "臺中", "台中", "臺南", "台南", "高雄",
    "基隆", "新竹", "嘉義", "苗栗", "彰化", "南投", "雲林", "屏東", "宜蘭",
    "花蓮", "臺東", "台東", "澎湖", "金門", "連江",
)


# ══════════════════════════════════════════
# 輔助判定
# ══════════════════════════════════════════

def parse_formed_time(raw: str) -> Optional[datetime]:
    """
    解析清冊的形成時間。格式雜亂，解析不了就回 None——
    不猜測、不補值。

    >>> parse_formed_time('2025/7/21')
    datetime.datetime(2025, 7, 21, 0, 0)
    >>> parse_formed_time('2014/10/21至26間') is None
    True
    """
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m", "%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_duration_days(raw: str) -> Optional[float]:
    """
    抽出存續日數。文字型（「持續至今」「<24HR」）回 None，
    由 verbalize.duration 以原文處理。

    >>> parse_duration_days('64')
    64.0
    >>> parse_duration_days('持續至今') is None
    True
    """
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clean_landmark(raw: str) -> str:
    """
    清冊地標常整串包在括號內（如「(林田山第118林班)」），
    直接嵌進句子會很突兀，敘述前先剝除外層括號。

    >>> clean_landmark('(林田山第118林班)')
    '林田山第118林班'
    >>> clean_landmark('燕子口、立霧溪第20林班')
    '燕子口、立霧溪第20林班'
    >>> clean_landmark('')
    ''
    """
    s = (raw or "").strip()
    while len(s) >= 2 and s[0] in "（(" and s[-1] in "）)":
        s = s[1:-1].strip()
    return s


def infer_river(name: str) -> Optional[str]:
    """
    由堰塞湖名稱推出河川名。清冊多以河川命名，
    但有例外（如「九份二山」「合流坪」），推不出就回 None。

    >>> infer_river('花蓮馬太鞍溪')
    '馬太鞍溪'
    >>> infer_river('荖濃溪支流寶來溪上游堰塞湖')
    '荖濃溪'
    >>> infer_river('九份二山') is None
    True
    """
    s = (name or "").strip()
    s = re.sub(r"（.*?）|\(.*?\)", "", s)
    for prefix in COUNTY_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    m = re.search(r"([\u4e00-\u9fff]{2,4}[溪川河])", s)
    return m.group(1) if m else None


def normalize_breach_cause(raw: str) -> Optional[str]:
    """
    潰決原因原文 → templates.yaml 的 breach_cause key。

    >>> normalize_breach_cause('溢流沖刷')
    'overflow'
    >>> normalize_breach_cause('機具開挖')
    'excavation'
    >>> normalize_breach_cause('') is None
    True
    """
    s = (raw or "").strip()
    if not s:
        return None
    if "溢流" in s:
        return "overflow"
    if "管湧" in s or "滲流" in s:
        return "piping"
    if "開挖" in s or "機具" in s or "回填" in s:
        return "excavation"
    if "豪雨" in s or "沖刷" in s:
        return "rainfall"
    return "unknown"


def typhoon_position(distance_km: Optional[float],
                     southwest_flow: Optional[bool]) -> str:
    """
    颱風相對位置分類，對應 templates.yaml 的 trigger.position_phrase。

    >>> typhoon_position(60, None)
    'direct'
    >>> typhoon_position(180, None)
    'outer'
    >>> typhoon_position(450, True)
    'southwest_flow'
    >>> typhoon_position(450, None)
    'distant'
    >>> typhoon_position(None, None)
    'distant'
    """
    if distance_km is None:
        return "distant"
    if distance_km < TYPHOON_DIRECT_KM:
        return "direct"
    if distance_km <= TYPHOON_OUTER_KM:
        return "outer"
    return "southwest_flow" if southwest_flow else "distant"


def status_key(status: str) -> str:
    """
    >>> status_key('存在(監測中)')
    'monitoring'
    >>> status_key('存在(已穩定)')
    'stable'
    >>> status_key('消失')
    'gone'
    """
    s = (status or "").strip()
    if "監測" in s:
        return "monitoring"
    if "存在" in s:
        return "stable"
    return "gone"


# ══════════════════════════════════════════
# 主判定：五個區段
# ══════════════════════════════════════════

def attribute(rec: LakeRecord, obs: Optional[Observations] = None) -> Attribution:
    """
    對一筆紀錄執行全部判定，回傳命中的規則與填槽值。

    obs 為 None 時仍可運作——只是不會命中需要觀測資料的規則，
    輸出的敘述會較簡短但不會有臆測內容。
    """
    obs = obs or Observations()
    a = Attribution()

    _trigger(rec, obs, a)
    _rainfall(rec, obs, a)
    _slide(rec, obs, a)
    _formation(rec, obs, a)
    _fate(rec, obs, a)

    return a


def _trigger(rec: LakeRecord, obs: Observations, a: Attribution) -> None:
    """誘因判定。優先序：地震 > 颱風 > 降雨 > 崩塌 > 未記載。"""
    cause = rec.cause or ""
    event = rec.event or ""

    # ── 地震型 ──
    if "地震" in cause or "地震" in event:
        quake_name = obs.quake_name or (event if "地震" in event else "地震")
        formed = obs.formed_time or parse_formed_time(rec.formed)
        delta_h = None
        if obs.quake_time and formed:
            delta_h = (formed - obs.quake_time).total_seconds() / 3600
            if not (0 <= delta_h <= QUAKE_WINDOW_HOURS):
                delta_h = None  # 超出關聯窗格，不敘述時間差

        if obs.pga_gal and obs.pga_gal >= PGA_REPORT_THRESHOLD and delta_h is not None:
            a.fire("trigger.quake.with_pga",
                   quake_name=quake_name,
                   hours_after=V.hours_after(delta_h),
                   pga=f"{obs.pga_gal:.0f}")
        elif delta_h is not None:
            a.fire("trigger.quake.text",
                   quake_name=quake_name,
                   hours_after=V.hours_after(delta_h))
        else:
            a.fire("trigger.quake.unknown_time", quake_name=quake_name)
        return

    # ── 颱風型 ──
    if "颱風" in cause or "颱風" in event:
        name = obs.typhoon_name or _extract_typhoon(event) or "颱風"
        pos = typhoon_position(obs.typhoon_distance_km, obs.southwest_flow)
        slots = {"typhoon_name": name, "position_key": pos}

        if obs.typhoon_distance_km is not None:
            a.fire("trigger.typhoon.with_distance",
                   distance=V.distance_km(obs.typhoon_distance_km), **slots)
        elif obs.southwest_flow:
            a.fire("trigger.typhoon.with_position", **slots)
        else:
            a.fire("trigger.typhoon.text", typhoon_name=name)
        return

    # ── 降雨型 ──
    if "降雨" in cause or "豪雨" in event or "降雨" in event:
        rain_event = event or "連日降雨"
        if obs.frontal_system:
            a.fire("trigger.rain.frontal", rain_event=rain_event)
        elif obs.typhoon_name is None and obs.typhoon_distance_km is None:
            a.fire("trigger.rain.text", rain_event=rain_event)
        else:
            a.fire("trigger.rain.no_typhoon", rain_event=rain_event)
        return

    # ── 崩塌型 ──
    if "崩塌" in cause or "地動" in cause:
        loc = clean_landmark(rec.landmark) or rec.village or "鄰近"
        a.fire("trigger.slide_only.text", location=loc)
        return

    a.fire("trigger.unknown.text")


def _extract_typhoon(event: str) -> Optional[str]:
    """
    從事件名稱抽出颱風名。

    >>> _extract_typhoon('薇帕颱風')
    '薇帕颱風'
    >>> _extract_typhoon('2012年0610豪雨及泰利颱風')
    '泰利颱風'
    >>> _extract_typhoon('九二一地震') is None
    True
    """
    for part in re.split(r"[及和、，,\s]+", event or ""):
        m = re.search(r"([\u4e00-\u9fff]{2,4}颱風)", part)
        if m:
            return m.group(1)
    return None


def _rainfall(rec: LakeRecord, obs: Observations, a: Attribution) -> None:
    """降雨條件。無資料時明確標示，不省略也不臆測。"""
    if obs.rain_24h_mm is None:
        # 只有在「確實查過觀測但沒有雨量」時才交代缺漏。
        # 完全沒介接資料源時保持沉默，否則每筆敘述都會掛一句雜訊。
        related = any(k.startswith(("trigger.typhoon", "trigger.rain"))
                      for k in a.rules_fired)
        if related and obs.has_any():
            a.fire("rainfall.no_data.text")
        return

    grade = V.rain_grade(obs.rain_24h_mm, obs.rain_window_hours)
    pct_key = V.rain_percentile(obs.rain_percentile)
    slots = {
        "window": obs.rain_window_hours,
        "mm": V.rain_amount(obs.rain_24h_mm),
        "grade_key": grade,
        "compare_key": pct_key,
    }

    if grade and pct_key:
        a.fire("rainfall.accumulation.with_compare", **slots)
    elif grade:
        a.fire("rainfall.accumulation.with_grade", **slots)
    else:
        a.fire("rainfall.accumulation.text", **slots)

    if obs.rain_max_hourly_mm:
        a.fire("rainfall.intensity.text",
               hourly=V.rain_amount(obs.rain_max_hourly_mm))


def _slide(rec: LakeRecord, obs: Observations, a: Attribution) -> None:
    """崩塌與堵塞。河川名推不出來就整句不輸出。"""
    river = rec.river or infer_river(rec.name)
    if not river:
        return

    location = clean_landmark(rec.landmark) or rec.village or ""
    if location:
        a.fire("slide.blockage.text", location=location, river=river)
    else:
        a.fire("slide.blockage.no_location", river=river)

    # 崩塌地與壩體的距離：兩組 TWD97 座標都在才算
    if rec.dam_xy and rec.slide_xy:
        dx = rec.dam_xy[0] - rec.slide_xy[0]
        dy = rec.dam_xy[1] - rec.slide_xy[1]
        offset = (dx * dx + dy * dy) ** 0.5
        if offset >= 100:  # 太近的差異無意義，不敘述
            a.fire("slide.scale.text", offset=V.distance_m(offset))


def _formation(rec: LakeRecord, obs: Observations, a: Attribution) -> None:
    """形成規模。"""
    if not rec.volume:
        a.fire("formation.no_volume.text")
        return

    scale = V.volume_scale(rec.volume)
    amount = V.volume_amount(rec.volume)
    if scale:
        a.fire("formation.volume.with_scale", volume=amount, scale_key=scale)
    else:
        a.fire("formation.volume.text", volume=amount)


def _fate(rec: LakeRecord, obs: Observations, a: Attribution) -> None:
    """後續狀態。潰決／穩定／監測中三種分支。"""
    days = parse_duration_days(rec.duration)
    dur_text = V.duration(days, rec.duration)
    st = status_key(rec.status)

    if st == "monitoring":
        if dur_text:
            a.fire("fate.monitoring.with_duration", duration=dur_text)
        else:
            a.fire("fate.monitoring.text")
        return

    if st == "stable":
        if dur_text:
            a.fire("fate.stable.with_duration", duration=dur_text)
        else:
            a.fire("fate.stable.text")
        return

    # 已消失
    date = V.date_only(rec.breach_date)
    cause_key = normalize_breach_cause(rec.breach_cause)

    if date and cause_key and dur_text:
        a.fire("fate.breached.full",
               date=date, cause_key=cause_key, duration=dur_text)
    elif date and dur_text:
        a.fire("fate.breached.with_date", date=date, duration=dur_text)
    elif cause_key and dur_text:
        a.fire("fate.breached.with_cause", cause_key=cause_key, duration=dur_text)
    elif dur_text:
        a.fire("fate.breached.text", duration=dur_text)
    else:
        a.fire("fate.gone_unknown.text")


if __name__ == "__main__":
    # 執行方式：python -m pipeline.attribution.rules
    import doctest
    fails, total = doctest.testmod()
    print(f"rules: {total - fails}/{total} 通過")
