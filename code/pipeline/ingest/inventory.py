#!/usr/bin/env python3
"""
將農村水保署堰塞湖清冊 CSV 轉為前端使用的 lakes.js

用法（於 code/ 目錄下）:
    python -m pipeline.ingest.inventory \
        ../data/raw/taiwan-barrier-lakes.csv dashboard/data/lakes.js

座標轉換: TWD97 TM2 (EPSG:3826) -> WGS84 經緯度 (EPSG:4326)
資料來源: https://tech.ardswc.gov.tw/Results/BarrierLakeInfo
"""

import csv
import json
import re
import sys
from pyproj import Transformer

TO_WGS84 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)

COLS = [
    "seq", "year", "name", "county", "town", "village", "landmark", "cause",
    "damX", "damY", "slideX", "slideY", "event", "formed", "duration",
    "volume", "breachDate", "breachCause", "status", "setting",
]


def num(s):
    """去除千分位與空白後轉 float；無值回傳 None。"""
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clean(s):
    return (s or "").strip()


def status_key(raw):
    """存續狀態 -> 分類代碼 watch / stable / gone"""
    s = clean(raw)
    if "監測" in s:
        return "watch"
    if "存在" in s:
        return "stable"
    return "gone"


def cause_key(raw):
    """誘因 -> 主分類（一筆可能寫多個原因，取第一個命中者）"""
    s = clean(raw)
    for needle, key in (("地震", "quake"), ("颱風", "typhoon"),
                        ("降雨", "rain"), ("崩塌", "slide")):
        if needle in s:
            return key
    return "other"


def parse_year_from_formed(formed):
    m = re.search(r"(\d{4})", formed or "")
    return int(m.group(1)) if m else None


def main(src, dst):
    rows = []
    with open(src, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # 跳過 DBF 匯出的欄位標頭
        for raw in reader:
            if len(raw) < len(COLS):
                continue
            rec = dict(zip(COLS, raw))
            if num(rec["seq"]) is None:
                continue  # 跳過表尾註記列

            x, y = num(rec["damX"]), num(rec["damY"])
            if x is None or y is None:
                # 少數斷層抬升型無壩體座標，改用崩塌地座標
                x, y = num(rec["slideX"]), num(rec["slideY"])
            if x is None or y is None:
                continue

            lon, lat = TO_WGS84.transform(x, y)
            year = int(num(rec["year"])) if num(rec["year"]) else parse_year_from_formed(rec["formed"])

            rows.append({
                "id": f'bl{int(num(rec["seq"])):03d}',
                "seq": int(num(rec["seq"])),
                "year": year,
                "name": clean(rec["name"]),
                "county": clean(rec["county"]),
                "town": clean(rec["town"]),
                "village": clean(rec["village"]),
                "landmark": clean(rec["landmark"]),
                "cause": clean(rec["cause"]),
                "causeKey": cause_key(rec["cause"]),
                "event": clean(rec["event"]),
                "formed": clean(rec["formed"]),
                "duration": clean(rec["duration"]),
                "volume": num(rec["volume"]),
                "breachDate": clean(rec["breachDate"]),
                "breachCause": clean(rec["breachCause"]),
                "status": clean(rec["status"]),
                "statusKey": status_key(rec["status"]),
                "setting": clean(rec["setting"]),
                "lon": round(lon, 5),
                "lat": round(lat, 5),
            })

    rows.sort(key=lambda r: (-(r["year"] or 0), -(r["volume"] or 0)))

    body = json.dumps(rows, ensure_ascii=False, indent=1)
    out = (
        "/* 由 tools/csv_to_js.py 自 data/taiwan-barrier-lakes.csv 產生，請勿手動編輯。\n"
        "   資料來源：農業部農村發展及水土保持署 堰塞湖清冊\n"
        "   https://tech.ardswc.gov.tw/Results/BarrierLakeInfo\n"
        "   座標已由 TWD97 TM2 轉為 WGS84。 */\n\n"
        f"window.BARRIER_LAKES = {body};\n"
    )
    with open(dst, "w", encoding="utf-8") as f:
        f.write(out)

    watch = sum(1 for r in rows if r["statusKey"] == "watch")
    stable = sum(1 for r in rows if r["statusKey"] == "stable")
    print(f"寫入 {dst}：{len(rows)} 筆（監測中 {watch}、已穩定 {stable}、消失 {len(rows)-watch-stable}）")


def cli() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "用法：python -m pipeline.ingest.inventory <來源CSV> <輸出JS>")
    main(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    cli()
