#!/usr/bin/env python3
"""
build_all.py — 一鍵重建前端所需的所有資料

依序執行：
    1. pipeline.ingest.inventory  → dashboard/data/lakes.js
    2. pipeline.ingest.risk       → dashboard/data/risk.js
       （模型與係數採用 package 版本；預設 offline 佔位，
        --live 才會真的呼叫 CWA API，見下方——這是 CAP severity/urgency
        唯一的依據，見「CAP 的風險依據」小節）
    3. pipeline.attribution.annotate → 對 lakes.js 加註成因敘述
    4. pipeline.assess.run        → dashboard/data/inundation.js（可選，見 --demo-dem）
       只處理清冊 statusKey == "watch" 的湖泊；需要真實 DEM
       （data/raw/dem/<lakeId>.tif 或 data/raw/dem/taiwan_dem.tif）
       或加 --demo-dem 用合成地形頂著（demo 用，非真實地形，結果一律
       標註 method="synthetic_demo_dem"，見 pipeline.assess.run 檔頭）。
       兩者都沒有時，這步會印出「跳過」並正常結束，不讓 build 失敗。
    5. pipeline.attribution.forecast_run → dashboard/data/forecast.js（同樣只在
       --demo-dem 時跑，且只對查得到公開可信集水面積來源的湖泊有結果，
       見 forecast_run.py 檔頭）

用法（於 code/ 目錄下，路徑皆相對於此）：
    python -m pipeline.build_all             # 風險用 offline 佔位，不連網，不跑 assess/forecast
    python -m pipeline.build_all --live      # 風險改抓 CWA 即時雨量（需 CWA_API_KEY）
    python -m pipeline.build_all --demo-dem  # 額外跑 assess + forecast（合成地形，demo 用）

這個腳本本身不含新邏輯，只是把既有模組的 main() 用預設路徑串起來，
方便一個指令（或 `更新並開啟儀表板.bat`）就把資料生完。
如果只想跑其中一步、或路徑跟預設不同，仍然可以照各模組 README 裡
寫的方式單獨呼叫 `python -m pipeline.ingest.inventory ...` 等指令。

預設不連網（--offline 傳給 risk.main），這樣沒有 CWA_API_KEY 的人
跑 build_all 也不會卡住或失敗；沒設金鑰時 pipeline.ingest.risk
本來就會自動退回 offline 佔位，這裡只是明確表達預設意圖。

assess/forecast 步驟預設不跑（沒有 --demo-dem 且沒有真實 DEM 時，`cap.js`
繼續用既有的 circle 頂著，行為跟這次改動之前完全一樣，不會有人
因為升級了程式碼而意外看到假地形產生的多邊形）。

── CAP 的風險依據：兩個模型，兩個角色，不是二選一 ──────────
`dashboard/cap.js` 的 severity/urgency/certainty（CAP 示警核心欄位）
一律用 risk.js（邏輯迴歸）決定，這件事本次沒有改變——它是唯一能自動
覆蓋清冊裡幾乎所有湖泊的信號。`forecast_run.py` 產生的 forecast.js
是另一件事：對「已經被判定該關注」的湖，估算大概還有多久可能溢流，
只在查得到公開可信集水面積來源時才有結果（目前只有花蓮馬太鞍溪）。
兩者在 CAP 輸出與前端都保持分開展示，forecast 的結果不會回頭覆寫
severity/urgency。細節見 `pipeline/attribution/forecast_run.py` 檔頭
與頂層 README「CAP 的風險依據」小節。
"""

from __future__ import annotations

import argparse
from datetime import datetime

from pipeline.ingest import inventory, risk
from pipeline.attribution import annotate, forecast_run
from pipeline.assess import run as assess_run

RAW = "../data/raw"
DASHBOARD_DATA = "dashboard/data"


def main(live: bool = False, demo_dem: bool = False) -> None:
    print("== [1/5] 轉換清冊 CSV → lakes.js ==")
    inventory.main(f"{RAW}/taiwan-barrier-lakes.csv", f"{DASHBOARD_DATA}/lakes.js")

    print("\n== [2/5] 風險模型（package）→ risk.js ==")
    risk.main(
        f"{DASHBOARD_DATA}/lakes.js",
        f"{DASHBOARD_DATA}/risk.js",
        offline=not live,
    )

    print("\n== [3/5] 產生成因敘述，加註回 lakes.js ==")
    annotate.main()

    lakes = _load_lakes_js(f"{DASHBOARD_DATA}/lakes.js")
    watch_count = sum(1 for lk in lakes if lk.get("statusKey") == "watch")

    print("\n== [4/5] 淹沒模擬（assess）→ inundation.js ==")
    if not demo_dem:
        assess_run.write_inundation_js(f"{DASHBOARD_DATA}/inundation.js", [])
        forecast_run.write_forecast_js(f"{DASHBOARD_DATA}/forecast.js", [])
        print(f"  跳過（{watch_count} 座監測中湖泊未評估）："
              f"沒有真實 DEM，且未加 --demo-dem，cap.js 繼續用 circle 頂著。"
              f"（仍寫出空的 inundation.js/forecast.js，避免前端 <script> 404）")
        print("\n完成。dashboard/data/ 已是最新資料。")
        return

    assess_results_list = assess_run.assess_watch_lakes(
        lakes, dem_dir=f"{RAW}/dem", allow_synthetic=True)
    assess_run.write_inundation_js(f"{DASHBOARD_DATA}/inundation.js", assess_results_list)
    n_synth = sum(1 for r in assess_results_list if r.method == "synthetic_demo_dem")
    print(f"  ✓ {len(assess_results_list)}/{watch_count} 座湖完成（{n_synth} 座用合成地形，"
          f"demo 用）→ {DASHBOARD_DATA}/inundation.js")

    print("\n== [5/5] 溢流預報（水量平衡）→ forecast.js ==")
    assess_results = {r.lake_id: r for r in assess_results_list}
    fcs = forecast_run.forecast_all(lakes, assess_results, observed_at=datetime.utcnow())
    forecast_run.write_forecast_js(f"{DASHBOARD_DATA}/forecast.js", fcs)
    if fcs:
        names = "、".join(f"{fc.lake_name}（集水面積來源：已查證）" for fc in fcs)
        print(f"  ✓ {len(fcs)} 座湖有查證過的集水面積可算預報：{names}"
              f" → {DASHBOARD_DATA}/forecast.js")
    else:
        print(f"  跳過：{len(assess_results_list)} 座已評估湖泊中沒有任何一座查得到公開可信"
              f"集水面積來源（見 forecast_run.KNOWN_CATCHMENTS_KM2），不猜數字充數。"
              f"（仍寫出空的 forecast.js，避免前端 <script> 404）")

    print("\n完成。dashboard/data/ 已是最新資料。")


def _load_lakes_js(path: str) -> list[dict]:
    """跟 pipeline.trigger.service.load_lakes_for_catchment 邏輯相同，
    這裡不直接 import 那個模組（trigger 跟 build_all 屬於不同關注點，
    不想因為 build_all 而拉進 trigger 的依賴），就地寫一份最小版本。"""
    import json
    import re
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"window\.BARRIER_LAKES\s*=\s*(\[.*?\]);", text, re.S)
    if not m:
        raise SystemExit(f"✗ 在 {path} 找不到 window.BARRIER_LAKES = [...]")
    return json.loads(m.group(1))


def cli() -> None:
    ap = argparse.ArgumentParser(description="一鍵重建前端所需的所有資料")
    ap.add_argument("--live", action="store_true",
                     help="風險模型改抓 CWA 即時雨量（需 CWA_API_KEY，見 pipeline.ingest.risk）")
    ap.add_argument("--demo-dem", action="store_true",
                     help="用合成地形跑 assess 步驟（demo 用，非真實地形，見 pipeline.assess.run）")
    args = ap.parse_args()
    main(live=args.live, demo_dem=args.demo_dem)


if __name__ == "__main__":
    cli()
