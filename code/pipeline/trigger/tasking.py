#!/usr/bin/env python3
"""
tasking.py — 把 thresholds 判斷 + catchment 圈定的結果組成「監測任務」
（架構文件 §04「命中門檻即建立『監測任務』」「任務建立後自動向 CDSE
查詢...」）。

MonitoringTask 建構本身（build_task_from_quake / build_task_from_rain）
仍然是純判斷＋資料結構組裝，不含網路 I/O——這點沒有變。但架構文件寫的
是「任務建立後『自動』查詢 CDSE」，所以本檔現在額外提供
`dispatch_for_task()` 把 `pipeline.trigger.dispatch` 接上，並讓
`build_tasks()` 可以選擇性地在建立任務的同時呼叫它（`enable_dispatch=True`
時）。之所以做成「選擇性」而不是每次都強制查，是因為 dispatch 會碰網路
（CDSE token 交換 + STAC 查詢），失敗或逾時不該讓觸發判斷本身也跟著卡住
——`dispatch_for_task()` 內部沿用 `dispatch.py` 已經有的「拿不到就降級」
設計，這裡不用再包一層 try/except。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pipeline.trigger.catchment import CatchmentAOI, circle_aoi, lakes_in_aoi
from pipeline.trigger.thresholds import (
    EARTHQUAKE_CATCHMENT_RADIUS_KM,
    EarthquakeObservation,
    RainfallStation,
    TriggerDecision,
    quake_exceeds_threshold,
    rain_exceeds_threshold,
)

_task_id_counter = itertools.count(1)


@dataclass
class MonitoringTask:
    task_id: str
    trigger_type: str                  # "quake" | "rain" | "compound"
    created_at: datetime
    aoi: CatchmentAOI
    basis: dict                        # 觸發依據（供稽核追溯，比照 attribution 的 rules_fired）
    nearby_known_lakes: list[dict] = field(default_factory=list)
    priority: str = "normal"           # compound 觸發一律 "high"
    dispatch: Optional[dict] = None    # dispatch_for_task() 的結果；None 代表沒查（見該函式說明）

    def to_dict(self) -> dict:
        return {
            "taskId": self.task_id,
            "triggerType": self.trigger_type,
            "createdAt": self.created_at.isoformat(),
            "aoi": {
                "centerLat": self.aoi.center_lat,
                "centerLon": self.aoi.center_lon,
                "radiusKm": self.aoi.radius_km,
                "method": self.aoi.method,
                "label": self.aoi.label,
            },
            "basis": self.basis,
            "nearbyKnownLakes": [
                {"id": lk.get("id"), "name": lk.get("name"),
                 "distanceKm": lk.get("distance_km"), "statusKey": lk.get("statusKey")}
                for lk in self.nearby_known_lakes
            ],
            "priority": self.priority,
            "dispatch": self.dispatch,
        }


def _new_task_id(prefix: str) -> str:
    return f"{prefix}-{next(_task_id_counter):04d}"


def dispatch_for_task(task: MonitoringTask, token: Optional[str] = None) -> dict:
    """對一個已建立的任務查詢可用的 Sentinel-1 影像（架構文件 §04）。

    回傳值一律是 dict（不是 None），即使查不到任何東西——查不到本身
    也是一種結果，要讓稽核看得出「有查過但沒找到」跟「根本沒查過」
    的差別：
        {
            "latestScene": {...} | None,   # 事件前最近一景無雲基準影像
            "nextScene": {...},            # 事件後第一景已排程／已拍攝的影像；
                                            # 真查不到時退回 estimate_next_pass()
                                            # 的經驗值估計（見 dispatch.py），
                                            # 該 dict 會自帶 estimated=True
            "queriedAt": ISO時間字串,
            "hasCdseCredentials": bool,     # token 是否成功取得，供除錯用
        }
    """
    from pipeline.trigger import dispatch as D

    now = task.created_at
    latest = D.query_latest_scene(task.aoi.center_lat, task.aoi.center_lon,
                                   task.aoi.radius_km, now, token)
    next_scene = D.query_next_scene(task.aoi.center_lat, task.aoi.center_lon,
                                     task.aoi.radius_km, now, token)
    if next_scene is None:
        next_scene = dict(D.estimate_next_pass(now))
        # estimate_next_pass() 回傳的 next_pass_eta 是 datetime 物件，
        # 這裡轉成 ISO 字串才能安全塞進 to_dict()／json.dumps（見
        # write_tasks_js）；STAC 查到的真實結果本身已經是 JSON 相容的
        # dict，不用轉。
        eta = next_scene.get("next_pass_eta")
        if hasattr(eta, "isoformat"):
            next_scene["next_pass_eta"] = eta.isoformat()

    return {
        "latestScene": latest,
        "nextScene": next_scene,
        "queriedAt": datetime.utcnow().isoformat(),
        "hasCdseCredentials": token is not None,
    }


def build_task_from_quake(quake: EarthquakeObservation, lakes: list[dict],
                           decision: Optional[TriggerDecision] = None,
                           enable_dispatch: bool = False,
                           cdse_token: Optional[str] = None) -> Optional[MonitoringTask]:
    """地震報告若達門檻，建立以震央為中心、半徑 30km 的監測任務；
    未達門檻回傳 None（呼叫端只需要檢查回傳值是否為 None）。

    `enable_dispatch=True` 時會在任務建立後立刻呼叫 `dispatch_for_task()`
    （見該函式），預設關閉——單元測試與離線流程不應該因為這個參數而
    意外連網，網路呼叫必須是呼叫端主動要求的。
    """
    decision = decision or quake_exceeds_threshold(quake)
    if not decision.triggered:
        return None

    aoi = circle_aoi(quake.epicenter_lat, quake.epicenter_lon,
                      EARTHQUAKE_CATCHMENT_RADIUS_KM,
                      label=f"震央 {EARTHQUAKE_CATCHMENT_RADIUS_KM:.0f}km")
    task = MonitoringTask(
        task_id=_new_task_id("QK"),
        trigger_type="quake",
        created_at=quake.time,
        aoi=aoi,
        basis={
            "magnitude": quake.magnitude,
            "epicenter": [quake.epicenter_lat, quake.epicenter_lon],
            "sourceId": quake.source_id,
            "reasons": decision.reasons,
        },
        nearby_known_lakes=lakes_in_aoi(aoi, lakes),
        priority="high",
    )
    if enable_dispatch:
        task.dispatch = dispatch_for_task(task, cdse_token)
    return task


def build_task_from_rain(station: RainfallStation, rain_time: datetime, lakes: list[dict],
                          last_significant_quake_time: Optional[datetime] = None,
                          catchment_radius_km: float = 10.0,
                          enable_dispatch: bool = False,
                          cdse_token: Optional[str] = None) -> Optional[MonitoringTask]:
    """雨量站若達門檻（含複合加權），建立以該測站為中心的監測任務。

    集水區半徑先用測站周邊 10km 近似（架構文件寫的是「圈定達標雨量站
    所屬集水區」，真正的集水區邊界要接水利署圖資，見 catchment.py 的
    TODO；10km 是保守的暫代值，之後接上真實集水區圖資後這個參數會被
    替換掉，不是最終答案）。

    `enable_dispatch` 同 `build_task_from_quake`。
    """
    decision = rain_exceeds_threshold(station, rain_time, last_significant_quake_time)
    if not decision.triggered:
        return None

    aoi = circle_aoi(station.lat, station.lon, catchment_radius_km,
                      label=f"{station.name} 測站集水區（近似）")
    task = MonitoringTask(
        task_id=_new_task_id("RN"),
        trigger_type=decision.trigger_type,   # "rain" 或 "compound"
        created_at=rain_time,
        aoi=aoi,
        basis={
            "station": station.name,
            "rain1h": station.rain_1h_mm,
            "rain24h": station.rain_24h_mm,
            "discountApplied": decision.discount_applied,
            "reasons": decision.reasons,
        },
        nearby_known_lakes=lakes_in_aoi(aoi, lakes),
        priority="high" if decision.trigger_type == "compound" else "normal",
    )
    if enable_dispatch:
        task.dispatch = dispatch_for_task(task, cdse_token)
    return task


def build_tasks(quakes: list[EarthquakeObservation],
                 rain_stations: list[RainfallStation],
                 rain_time: datetime,
                 lakes: list[dict],
                 last_significant_quake_time: Optional[datetime] = None,
                 enable_dispatch: bool = False,
                 cdse_token: Optional[str] = None,
                 ) -> list[MonitoringTask]:
    """整批建立監測任務：每筆達門檻的地震一個任務、每個達門檻的雨量站一個任務。

    地震彼此獨立成任務（不同震央、不同圈定範圍，不合併）；
    雨量站的複合加權則是靠 `last_significant_quake_time`（近期最新一筆
    達門檻地震的時間）帶入，不需要在這裡重新比對地震清單。

    `enable_dispatch=True` 時，每個新建立的任務都會立刻查一次 CDSE
    （見 `dispatch_for_task`）；`cdse_token` 建議由呼叫端（service.py）
    只換一次再傳進來，不要讓每個任務各自重新換 token。
    """
    tasks = [
        t for t in (
            build_task_from_quake(q, lakes, enable_dispatch=enable_dispatch, cdse_token=cdse_token)
            for q in quakes
        )
        if t is not None
    ]
    tasks += [
        t for t in (
            build_task_from_rain(s, rain_time, lakes, last_significant_quake_time,
                                  enable_dispatch=enable_dispatch, cdse_token=cdse_token)
            for s in rain_stations
        )
        if t is not None
    ]
    return tasks
