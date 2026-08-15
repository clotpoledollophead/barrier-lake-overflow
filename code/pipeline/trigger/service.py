#!/usr/bin/env python3
"""
service.py — 觸發層輪詢入口（架構文件 §04「觸發監聽是常駐的輕量服務
（cron 每 10 分鐘輪詢 CWA API），命中門檻即建立『監測任務』」「任務建立
後自動向 CDSE 查詢...」）。

輪詢機制：有裝 `apscheduler`（見 requirements.txt）就用
`BlockingScheduler` 常駐排程；沒裝就自動退回陽春的
`while True: check_once(); time.sleep(...)` 迴圈，並印一行提示可以
`pip install apscheduler` 升級——兩條路徑都呼叫同一個 `check_once()`，
不會因為排程機制不同而讓觸發判斷本身的行為跟著變。

用法（於 code/ 目錄下）：
    export CWA_API_KEY="CWA-你的授權碼"
    python -m pipeline.trigger.service --once             # 檢查一次就結束
    python -m pipeline.trigger.service --poll             # 常駐輪詢（預設每 10 分鐘）
    python -m pipeline.trigger.service --once --offline   # 不連網，只測流程
    python -m pipeline.trigger.service --once --demo      # 用內建假資料跑一次
                                                            # （0403 花蓮地震規模的
                                                            # 合成情境，供 demo /
                                                            # 展示用，非真實觀測）
    python -m pipeline.trigger.service --once --no-dispatch
                                                            # 不查 CDSE 影像調度，
                                                            # 只做門檻判斷（更快，
                                                            # 適合單純測 thresholds）

沒有 CWA_API_KEY 或指定 --offline 時，跟 pipeline.ingest.cwa /
pipeline.ingest.risk 一樣的降級原則：不連網、印警告、正常結束
（回傳 0 個任務），不讓 build 或 demo 卡住。

影像調度（dispatch）：預設在建立任務後自動查一次 CDSE（見
`pipeline.trigger.tasking.dispatch_for_task`），--offline 時自動跳過
（不連網原則優先），--no-dispatch 可以在非 offline 時也手動關掉；
CDSE token 只在每次 check_once() 換一次，不會每個任務各自重新換。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from pipeline.ingest import cwa
from pipeline.trigger import dispatch as D
from pipeline.trigger import earthquake
from pipeline.trigger.tasking import MonitoringTask, build_tasks
from pipeline.trigger.thresholds import EarthquakeObservation, EarthquakeStation, RainfallStation

DEFAULT_LAKES = "dashboard/data/lakes.js"
DEFAULT_OUT = "dashboard/data/trigger_tasks.js"
DEFAULT_STATE = "../data/derived/trigger_state.json"
DEFAULT_POLL_INTERVAL_SEC = 600  # 架構文件：cron 每 10 分鐘輪詢


# ── 湖泊清冊（沿用 lakes.js，跟 pipeline.ingest.risk.load_lakes 分開寫，
#    因為這裡需要 statusKey 而 risk.load_lakes 沒有保留這個欄位）──────

def load_lakes_for_catchment(lakes_js_path: str) -> list[dict]:
    with open(lakes_js_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"window\.BARRIER_LAKES\s*=\s*(\[.*?\]);", text, re.S)
    if not m:
        raise SystemExit(f"✗ 在 {lakes_js_path} 找不到 window.BARRIER_LAKES = [...]")
    return json.loads(m.group(1))


# ── 狀態（跨輪次記住「最近一次達門檻的地震」，供複合加權判斷用）───

def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"last_significant_quake_time": None, "last_significant_quake_epicenter": None}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def _state_quake_time(state: dict) -> Optional[datetime]:
    t = state.get("last_significant_quake_time")
    return datetime.fromisoformat(t) if t else None


# ── demo 用合成資料（明確標註非真實觀測，僅供展示/流程測試）──────

def _demo_quake() -> EarthquakeObservation:
    """合成一筆規模與 0403 花蓮地震相近的示範地震，震央刻意擺在馬太鞍溪
    堰塞湖（清冊 bl071「花蓮馬太鞍溪」，lat 23.70/lon 121.30）附近，
    讓 --demo 能示範「觸發後圈定範圍內剛好命中主驗證案例」這個敘事——
    座標與震度都是虛構近似值，不是 CWA 的真實報告。"""
    return EarthquakeObservation(
        time=datetime.utcnow(),
        magnitude=7.2,
        epicenter_lat=23.72,
        epicenter_lon=121.32,
        stations=[
            EarthquakeStation("萬榮（示範）", 23.70, 121.30, "6強"),
            EarthquakeStation("光復（示範）", 23.67, 121.42, "6弱"),
        ],
        source_id="DEMO-NOT-REAL",
    )


def _demo_rain_stations() -> list[RainfallStation]:
    return [
        RainfallStation("光復（示範）", 23.67, 121.42, rain_1h_mm=45.0, rain_24h_mm=180.0),
    ]


# ── 單次檢查（純邏輯 + 可注入的 fetcher，方便離線測試）────────────

def check_once(
    lakes_path: str = DEFAULT_LAKES,
    state_path: str = DEFAULT_STATE,
    out_path: str = DEFAULT_OUT,
    offline: bool = False,
    demo: bool = False,
    dispatch_images: bool = True,
    quake_fetcher: Optional[Callable[[str], list[EarthquakeObservation]]] = None,
    rain_fetcher: Optional[Callable[[str], list[dict]]] = None,
) -> list[MonitoringTask]:
    """跑一次觸發檢查：抓地震 + 雨量 → 判斷門檻 → 建立監測任務
    → （預設）查一次 CDSE 影像調度 → 寫檔。

    `quake_fetcher` / `rain_fetcher` 讓測試可以注入假資料，不用真的連網
    （比照 pipeline.ingest.risk 的 offline 佔位精神，但這裡用依賴注入
    而不是內建平均值——地震沒有「訓練平均值」這種東西可以退回）。

    `dispatch_images`：是否在建立任務後查 CDSE。offline=True 時無論這個
    參數是什麼一律不查（不連網原則優先於這個開關）；`--no-dispatch`
    CLI 旗標把它設成 False，用於只想測門檻判斷、不想等網路逾時的情境。
    """
    lakes = load_lakes_for_catchment(lakes_path)
    state = load_state(state_path)
    api_key = os.environ.get("CWA_API_KEY", "").strip()

    quakes: list[EarthquakeObservation] = []
    rain_stations: list[RainfallStation] = []
    rain_time = datetime.utcnow()

    if demo:
        print("⚠ --demo：使用內建合成情境，不是真實觀測資料")
        quakes = [_demo_quake()]
        rain_stations = _demo_rain_stations()
    elif offline:
        print("⚠ --offline：不連網，本次檢查地震/雨量皆視為無資料（0 個任務）")
    else:
        if quake_fetcher is not None:
            quakes = quake_fetcher(api_key)
        elif api_key:
            try:
                quakes = earthquake.fetch_significant_earthquakes(api_key)
            except Exception as exc:
                print(f"⚠ 抓取地震報告失敗（{exc}），本次視為無地震資料")
        else:
            print("⚠ 未設定 CWA_API_KEY → 本次跳過地震/雨量擷取")

        if quake_fetcher is not None or api_key:
            if rain_fetcher is not None:
                raw_stations = rain_fetcher(api_key)
            else:
                try:
                    raw_stations = cwa.fetch_rainfall_stations(api_key) if api_key else []
                except Exception as exc:
                    print(f"⚠ 抓取雨量觀測失敗（{exc}），本次視為無雨量資料")
                    raw_stations = []
            rain_stations = [
                RainfallStation(
                    name=s.get("name", f"({s['lat']:.2f},{s['lon']:.2f})"),
                    lat=s["lat"], lon=s["lon"],
                    rain_1h_mm=None,  # O-A0002-001 目前只解析 24h 累積（見 ingest/cwa.py）
                    rain_24h_mm=s.get("r24h"),
                )
                for s in raw_stations
            ]

    # 用「本次地震」或「狀態檔記住的上一筆」判斷複合加權
    triggering_quakes = [q for q in quakes]
    last_quake_time = _state_quake_time(state)
    if triggering_quakes:
        latest = max(triggering_quakes, key=lambda q: q.time)
        if last_quake_time is None or latest.time > last_quake_time:
            last_quake_time = latest.time
            state["last_significant_quake_time"] = latest.time.isoformat()
            state["last_significant_quake_epicenter"] = [latest.epicenter_lat, latest.epicenter_lon]
            save_state(state_path, state)

    # CDSE token 只換一次，傳給 build_tasks 讓每個任務共用（見 tasking.py）。
    enable_dispatch = dispatch_images and not offline
    cdse_token = D.get_access_token() if enable_dispatch else None
    if enable_dispatch and cdse_token is None:
        print("⚠ 未設定 CDSE_CLIENT_ID/CDSE_CLIENT_SECRET（或換 token 失敗），"
              "影像調度將改用重訪週期估計值頂著（見 dispatch.estimate_next_pass）")

    tasks = build_tasks(
        quakes=triggering_quakes,
        rain_stations=rain_stations,
        rain_time=rain_time,
        lakes=lakes,
        last_significant_quake_time=last_quake_time,
        enable_dispatch=enable_dispatch,
        cdse_token=cdse_token,
    )

    write_tasks_js(out_path, tasks)
    print(f"✓ 本次檢查完成：{len(tasks)} 個監測任務 → {out_path}")
    for t in tasks:
        lake_names = "、".join(lk.get("name", "") for lk in t.nearby_known_lakes[:3]) or "（無）"
        dispatch_note = ""
        if t.dispatch is not None:
            has_latest = t.dispatch.get("latestScene") is not None
            est = bool(t.dispatch.get("nextScene", {}) and t.dispatch["nextScene"].get("estimated"))
            dispatch_note = (f"｜影像：{'有基準景' if has_latest else '無基準景'}／"
                              f"下次過境{'為估計值' if est else '已排程'}")
        print(f"  [{t.priority}] {t.task_id}｜{t.trigger_type}｜"
              f"AOI 半徑 {t.aoi.radius_km:.0f}km｜附近已知湖泊：{lake_names}{dispatch_note}")
    return tasks


def write_tasks_js(path: str, tasks: list[MonitoringTask]) -> None:
    """寫出 dashboard 可讀的監測任務資料。前端（app.js/map3d.js）會讀取
    本檔在地圖上疊「觸發範圍」圖層，見 dashboard/app.js 的
    renderTriggerPanel()／Map3D.setTriggerAreas()。"""
    body = json.dumps([t.to_dict() for t in tasks], ensure_ascii=False, indent=1)
    out = (
        "/* 由 pipeline/trigger/service.py 產生，請勿手動編輯。 */\n\n"
        f"window.TRIGGER_TASKS = {body};\n"
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)


def poll_loop(interval_sec: int = DEFAULT_POLL_INTERVAL_SEC, **kwargs) -> None:
    """常駐輪詢。有裝 apscheduler 就用 BlockingScheduler（見架構文件 §11
    技術棧表），沒裝就退回陽春 while 迴圈——兩者都是呼叫同一個
    check_once()，只是排程機制不同，行為（門檻判斷、寫檔）不會因此改變。
    Ctrl+C 兩條路徑都能正常結束。"""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        print(f"⚠ 未安裝 apscheduler（pip install apscheduler 可改用排程器），"
              f"退回陽春迴圈：每 {interval_sec} 秒檢查一次（Ctrl+C 結束）")
        try:
            while True:
                check_once(**kwargs)
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\n已停止輪詢")
        return

    print(f"觸發層常駐輪詢啟動（apscheduler），每 {interval_sec} 秒檢查一次（Ctrl+C 結束）")
    scheduler = BlockingScheduler()
    scheduler.add_job(
        check_once, trigger=IntervalTrigger(seconds=interval_sec),
        kwargs=kwargs, next_run_time=datetime.now(),  # 啟動時立刻跑一次，不用先空等一個 interval
        id="ossint-trigger-check", max_instances=1,   # 上一輪還沒跑完就不疊加新一輪
    )
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n已停止輪詢")


def cli() -> None:
    ap = argparse.ArgumentParser(description="觸發層：門檻判斷與監測任務建立")
    ap.add_argument("--lakes", default=DEFAULT_LAKES)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--offline", action="store_true", help="不連網，只測流程")
    ap.add_argument("--demo", action="store_true", help="用內建合成情境跑一次（非真實資料）")
    ap.add_argument("--no-dispatch", action="store_true",
                     help="不查 CDSE 影像調度（預設會查，見 check_once 的 dispatch_images）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="檢查一次就結束（預設）")
    mode.add_argument("--poll", action="store_true", help="常駐輪詢")
    ap.add_argument("--interval-sec", type=int, default=DEFAULT_POLL_INTERVAL_SEC)
    args = ap.parse_args()

    kwargs = dict(lakes_path=args.lakes, state_path=args.state, out_path=args.out,
                   offline=args.offline, demo=args.demo, dispatch_images=not args.no_dispatch)
    if args.poll:
        poll_loop(interval_sec=args.interval_sec, **kwargs)
    else:
        check_once(**kwargs)


if __name__ == "__main__":
    cli()
