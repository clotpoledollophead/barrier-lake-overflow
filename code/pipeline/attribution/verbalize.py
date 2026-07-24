#!/usr/bin/env python3
"""
verbalize.py — 數值轉自然語言

把計算結果轉成人看得懂的中文表述。所有規則都是確定性的：
同樣的輸入必得同樣的輸出，可寫單元測試。

設計原則
--------
1. 級距名稱一律採官方定義（雨量分級用中央氣象署），不自創形容詞。
2. 精度隨量級調整——「9,100 萬立方公尺」不需要小數，「0.27」需要。
3. 「無此現象」與「未記載」必須可區分，不可都輸出「無」。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


# ══════════════════════════════════════════
# 時長
# ══════════════════════════════════════════

def duration(days: Optional[float], raw: str = "") -> Optional[str]:
    """
    存續時間轉中文。清冊原始欄位混用數字與文字
    （如「持續至今」「<24HR」「1.5HR」），故保留 raw 作為後備。

    >>> duration(2)
    '2 日'
    >>> duration(9)
    '9 日'
    >>> duration(64)
    '64 日（約 2.1 個月）'
    >>> duration(1746)
    '1,746 日（約 4.8 年）'
    >>> duration(None, '持續至今')
    '持續至今'
    >>> duration(None, '<24HR')
    '不足 24 小時'
    """
    if days is None:
        return _duration_from_raw(raw)

    d = float(days)
    if d < 1:
        return "不足 1 日"
    if d < 14:
        return f"{int(d)} 日"
    if d <= 60:
        weeks = round(d / 7)
        return f"{int(d)} 日（約 {weeks} 週）"
    if d < 365:
        months = d / 30.44
        return f"{int(d):,} 日（約 {months:.1f} 個月）"
    years = d / 365.25
    return f"{int(d):,} 日（約 {years:.1f} 年）"


def _duration_from_raw(raw: str) -> Optional[str]:
    """處理清冊中的文字型存續時間。"""
    s = (raw or "").strip()
    if not s:
        return None
    if "持續至今" in s:
        return "持續至今"
    if s.upper().startswith("<"):
        return "不足 24 小時" if "24" in s else f"不足 {s.lstrip('<')}"
    if s.upper().endswith("HR"):
        hours = s.upper().replace("HR", "").strip()
        return f"約 {hours} 小時"
    return s


def hours_after(delta_hours: Optional[float]) -> Optional[str]:
    """
    事件間隔轉中文，用於「地震後 N 小時內形成」。

    >>> hours_after(3)
    '3 小時'
    >>> hours_after(0.5)
    '1 小時'
    >>> hours_after(54)
    '2 日'
    """
    if delta_hours is None:
        return None
    h = float(delta_hours)
    if h < 1:
        return "1 小時"
    if h < 48:
        return f"{int(round(h))} 小時"
    return f"{int(round(h / 24))} 日"


# ══════════════════════════════════════════
# 雨量
# ══════════════════════════════════════════

# 中央氣象署雨量分級（24 小時累積）
RAIN_GRADES_24H = [
    (500, "extremely_torrential"),  # 超大豪雨
    (350, "torrential"),            # 大豪雨
    (200, "extremely_heavy"),       # 豪雨
    (80,  "heavy"),                 # 大雨
]


def rain_grade(mm: Optional[float], window_hours: int = 24) -> Optional[str]:
    """
    回傳雨量分級的 key（供 templates.yaml 查表），未達大雨標準回傳 None。

    僅 24 小時窗格適用官方分級；其他窗格回傳 None，
    避免把 6 小時雨量硬套 24 小時的級距。

    >>> rain_grade(460)
    'torrential'
    >>> rain_grade(85)
    'heavy'
    >>> rain_grade(40)
    >>> rain_grade(460, window_hours=6)
    """
    if mm is None or window_hours != 24:
        return None
    for threshold, key in RAIN_GRADES_24H:
        if mm >= threshold:
            return key
    return None


def rain_amount(mm: Optional[float]) -> Optional[str]:
    """
    雨量數值格式化。

    >>> rain_amount(460.0)
    '460'
    >>> rain_amount(87.5)
    '88'
    """
    if mm is None:
        return None
    return f"{round(mm):,}"


def rain_percentile(pct: Optional[float]) -> Optional[str]:
    """
    回傳比較子句的 key，落在一般區間時回傳 None（該子句整句不輸出）。

    pct 為該筆雨量在測站歷史紀錄中的百分位（0–100，越大越極端）。

    >>> rain_percentile(99.5)
    'top1'
    >>> rain_percentile(96)
    'top5'
    >>> rain_percentile(70)
    """
    if pct is None:
        return None
    if pct >= 99:
        return "top1"
    if pct >= 95:
        return "top5"
    return None


# ══════════════════════════════════════════
# 蓄水量與規模
# ══════════════════════════════════════════

VOLUME_SCALES = [
    (5000, "huge"),    # 極大型
    (1000, "large"),   # 大型
    (100,  "medium"),  # 中型
    (10,   "small"),   # 小型
]


def volume_scale(wan_m3: Optional[float]) -> Optional[str]:
    """
    蓄水量規模分級的 key。單位為萬立方公尺。

    注意：此分級為本專案自訂，非官方定義，對外呈現時須註明。

    >>> volume_scale(9100)
    'huge'
    >>> volume_scale(190)
    'medium'
    >>> volume_scale(4)
    'tiny'
    >>> volume_scale(0)
    """
    if not wan_m3:
        return None
    for threshold, key in VOLUME_SCALES:
        if wan_m3 >= threshold:
            return key
    return "tiny"


def volume_amount(wan_m3: Optional[float]) -> Optional[str]:
    """
    蓄水量格式化：大數不取小數，小數保留兩位。

    >>> volume_amount(9100)
    '9,100'
    >>> volume_amount(108.97)
    '108.97'
    >>> volume_amount(0.27)
    '0.27'
    >>> volume_amount(0)
    """
    if not wan_m3:
        return None
    if wan_m3 >= 1000:
        return f"{round(wan_m3):,}"
    if wan_m3 >= 1:
        return f"{wan_m3:.2f}".rstrip("0").rstrip(".")
    return f"{wan_m3:.2f}"


# ══════════════════════════════════════════
# 距離與高程
# ══════════════════════════════════════════

def distance_km(km: Optional[float]) -> Optional[str]:
    """
    >>> distance_km(180.4)
    '180'
    >>> distance_km(8.6)
    '8.6'
    """
    if km is None:
        return None
    return f"{round(km):,}" if km >= 10 else f"{km:.1f}"


def distance_m(m: Optional[float]) -> Optional[str]:
    """
    >>> distance_m(2284.0)
    '2,284'
    """
    if m is None:
        return None
    return f"{round(m):,}"


def elevation_gap(m: Optional[float]) -> Optional[str]:
    """
    距壩頂高差。溢流風險的核心數字，一律保留一位小數。

    >>> elevation_gap(6.4)
    '6.4'
    >>> elevation_gap(0.35)
    '0.4'
    """
    if m is None:
        return None
    q = Decimal(str(m)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{q}"


# ══════════════════════════════════════════
# 時間點與時效
# ══════════════════════════════════════════

def timepoint(dt: Optional[datetime]) -> Optional[str]:
    """
    >>> timepoint(datetime(2026, 8, 15, 2, 0))
    '08/15 02:00'
    """
    return dt.strftime("%m/%d %H:%M") if dt else None


def date_only(s: str) -> Optional[str]:
    """
    清冊日期字串正規化為 YYYY/MM/DD。無法解析時原樣回傳。

    >>> date_only('2025/09/23')
    '2025/09/23'
    >>> date_only('2014/10/21至26間')
    '2014/10/21至26間'
    """
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue
    return s


def age(delta: Optional[timedelta]) -> Optional[str]:
    """
    資料時效。用於提醒使用者這個數字有多新。

    >>> age(timedelta(hours=2))
    '2 小時'
    >>> age(timedelta(days=3, hours=5))
    '3 日'
    """
    if delta is None:
        return None
    total_h = delta.total_seconds() / 3600
    if total_h < 1:
        return "不足 1 小時"
    if total_h < 48:
        return f"{int(total_h)} 小時"
    return f"{int(total_h / 24)} 日"


def staleness_key(delta: Optional[timedelta]) -> str:
    """
    資料時效分級，對應 templates.yaml 的 forecast.staleness。

    Sentinel-1 約 6 日重訪，故以 12 小時與 7 日為界。

    >>> staleness_key(timedelta(hours=6))
    'fresh'
    >>> staleness_key(timedelta(days=3))
    'aging'
    >>> staleness_key(timedelta(days=9))
    'stale'
    """
    if delta is None:
        return "stale"
    hours = delta.total_seconds() / 3600
    if hours <= 12:
        return "fresh"
    if hours <= 24 * 7:
        return "aging"
    return "stale"


# ══════════════════════════════════════════
# 缺值處理
# ══════════════════════════════════════════

def absence(kind: str = "not_recorded") -> str:
    """
    缺值的表述。三種語意必須區分：

      not_recorded      清冊未登載（不代表沒發生）
      none_occurred     確定無此現象
      insufficient_data 有資料但不足以判定

    >>> absence()
    '未記載'
    >>> absence('none_occurred')
    '無'
    """
    return {
        "not_recorded": "未記載",
        "none_occurred": "無",
        "insufficient_data": "資料不足",
    }.get(kind, "未記載")


if __name__ == "__main__":
    # 執行方式：python -m pipeline.attribution.verbalize
    import doctest
    fails, total = doctest.testmod()
    print(f"verbalize: {total - fails}/{total} 通過")
