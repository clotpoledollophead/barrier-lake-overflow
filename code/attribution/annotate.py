#!/usr/bin/env python3
"""
annotate.py — 批次產生全清冊的成因敘述

讀 data/lakes.js，對每筆紀錄跑一次歸因與敘述，把結果寫回
data/lakes.js（新增 narrative 與 rulesFired 欄位），供前端顯示。

用法：
    python3 attribution/annotate.py

觀測資料（雨量、颱風距離、地震規模）目前尚未介接，因此多數紀錄
只會產出不依賴觀測的句子。介接 CWA API 後，把觀測值填進
load_observations() 即可自動變詳細——敘述邏輯完全不用改。
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rules import LakeRecord, Observations, attribute
from compose import Composer


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "lakes.js")
TEMPLATES = os.path.join(HERE, "templates.yaml")


def read_lakes(path: str) -> list:
    """從 lakes.js 取出 JSON 陣列。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    start, end = src.index("["), src.rindex("]") + 1
    return json.loads(src[start:end])


def write_lakes(path: str, rows: list) -> None:
    header = (
        "/* 由 tools/csv_to_js.py 產生，並由 attribution/annotate.py 加註敘述。\n"
        "   請勿手動編輯。\n"
        "   資料來源：農業部農村發展及水土保持署 堰塞湖清冊\n"
        "   https://tech.ardswc.gov.tw/Results/BarrierLakeInfo\n"
        "   座標已由 TWD97 TM2 轉為 WGS84。 */\n\n"
    )
    body = json.dumps(rows, ensure_ascii=False, indent=1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + f"window.BARRIER_LAKES = {body};\n")


def to_record(row: dict) -> LakeRecord:
    return LakeRecord(
        seq=row.get("seq", 0),
        name=row.get("name", ""),
        year=row.get("year"),
        county=row.get("county", ""),
        town=row.get("town", ""),
        village=row.get("village", ""),
        landmark=row.get("landmark", ""),
        cause=row.get("cause", ""),
        event=row.get("event", ""),
        formed=row.get("formed", ""),
        duration=row.get("duration", ""),
        volume=row.get("volume"),
        breach_date=row.get("breachDate", ""),
        breach_cause=row.get("breachCause", ""),
        status=row.get("status", ""),
        setting=row.get("setting", ""),
        lon=row.get("lon"),
        lat=row.get("lat"),
    )


def load_observations(row: dict) -> Optional[Observations]:
    """
    取得該筆紀錄形成期間的氣象／地震觀測。

    目前回傳 None——尚未介接 CWA API。介接後在此依 formed 時間與
    壩體座標查詢對應測站，填入 Observations 即可；敘述會自動變詳細，
    不需要動 rules.py 或 templates.yaml。

    待介接：
      · CWA 自動雨量站歷史資料 → rain_24h_mm / rain_max_hourly_mm
      · CWA 颱風資料庫路徑     → typhoon_name / typhoon_distance_km
      · CWA 地震報告與測站震度 → quake_time / quake_magnitude / pga_gal
    """
    return None


def main() -> None:
    if not os.path.exists(DATA):
        raise SystemExit(f"找不到 {DATA}，請先執行 tools/csv_to_js.py")

    rows = read_lakes(DATA)
    composer = Composer(TEMPLATES)

    annotated = 0
    unresolved_total = []

    for row in rows:
        rec = to_record(row)
        obs = load_observations(row)
        result = composer.render(attribute(rec, obs))

        row["narrative"] = result.text
        row["rulesFired"] = result.rules_fired
        if result.text:
            annotated += 1
        if result.unresolved:
            unresolved_total.append((rec.seq, rec.name, result.unresolved))

    write_lakes(DATA, rows)

    print(f"已加註 {annotated}/{len(rows)} 筆敘述 → {DATA}")
    if unresolved_total:
        print(f"\n有 {len(unresolved_total)} 筆出現略過的句子：")
        for seq, name, items in unresolved_total[:10]:
            print(f"  #{seq} {name}: {', '.join(items)}")
        if len(unresolved_total) > 10:
            print(f"  ...另有 {len(unresolved_total) - 10} 筆")
    else:
        print("所有句子皆完整填槽，無略過。")

    # 抽樣顯示，方便肉眼檢查
    print("\n抽樣（2025 年）：")
    for row in rows:
        if row.get("year") == 2025 and row.get("narrative"):
            print(f"\n  #{row['seq']} {row['name']}")
            print(f"  {row['narrative']}")


if __name__ == "__main__":
    main()
