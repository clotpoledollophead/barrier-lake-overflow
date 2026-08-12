#!/usr/bin/env python3
"""
build_all.py — 一鍵重建前端所需的所有資料

依序執行：
    1. pipeline.ingest.inventory  → dashboard/data/lakes.js
    2. pipeline.ingest.risk       → dashboard/data/risk.js
       （模型與係數採用 package 版本；預設 offline 佔位，
        --live 才會真的呼叫 CWA API，見下方）
    3. pipeline.attribution.annotate → 對 lakes.js 加註成因敘述

用法（於 code/ 目錄下，路徑皆相對於此）：
    python -m pipeline.build_all             # 風險用 offline 佔位，不連網
    python -m pipeline.build_all --live      # 風險改抓 CWA 即時雨量（需 CWA_API_KEY）

這個腳本本身不含新邏輯，只是把既有模組的 main() 用預設路徑串起來，
方便一個指令（或 `更新並開啟儀表板.bat`）就把資料生完。
如果只想跑其中一步、或路徑跟預設不同，仍然可以照各模組 README 裡
寫的方式單獨呼叫 `python -m pipeline.ingest.inventory ...` 等指令。

預設不連網（--offline 傳給 risk.main），這樣沒有 CWA_API_KEY 的人
跑 build_all 也不會卡住或失敗；沒設金鑰時 pipeline.ingest.risk
本來就會自動退回 offline 佔位，這裡只是明確表達預設意圖。
"""

from __future__ import annotations

import argparse

from pipeline.ingest import inventory, risk
from pipeline.attribution import annotate

RAW = "../data/raw"
DASHBOARD_DATA = "dashboard/data"


def main(live: bool = False) -> None:
    print("== [1/3] 轉換清冊 CSV → lakes.js ==")
    inventory.main(f"{RAW}/taiwan-barrier-lakes.csv", f"{DASHBOARD_DATA}/lakes.js")

    print("\n== [2/3] 風險模型（package）→ risk.js ==")
    risk.main(
        f"{DASHBOARD_DATA}/lakes.js",
        f"{DASHBOARD_DATA}/risk.js",
        offline=not live,
    )

    print("\n== [3/3] 產生成因敘述，加註回 lakes.js ==")
    annotate.main()

    print("\n完成。dashboard/data/ 已是最新資料。")


def cli() -> None:
    ap = argparse.ArgumentParser(description="一鍵重建前端所需的所有資料")
    ap.add_argument("--live", action="store_true",
                     help="風險模型改抓 CWA 即時雨量（需 CWA_API_KEY，見 pipeline.ingest.risk）")
    args = ap.parse_args()
    main(live=args.live)


if __name__ == "__main__":
    cli()
