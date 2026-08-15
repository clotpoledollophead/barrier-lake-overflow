#!/usr/bin/env python3
"""
擷取中央氣象署（CWA）顯著有感地震報告（E-A0015-001），轉成
pipeline.trigger.thresholds.EarthquakeObservation 可直接判斷的格式。

跟 pipeline.ingest.cwa 是同一層級的模組（觸發層的「抓資料」部分），
判斷邏輯不寫在這裡——這裡只負責「抓、解析、轉型別」。

已知限制（誠實列出）：
    · CWA 地震測站資料通常不含海拔，thresholds.is_mountain_station()
      因此會退回用經緯度概略判斷（見該函式註解），不是精確地形分類。
    · 本檔的 JSON 解析是依 CWA 開放資料平台文件公開的欄位命名撰寫，
      跟 ingest/cwa.py 一樣用「找得到就收、找不到就跳過」的寬鬆解析，
      降低欄位改版時整條 pipeline 掛掉的風險；但目前尚未拿真實金鑰
      對過一次實際回應（本專案沒有 CI 網路權限），第一次接上真金鑰
      時務必核對欄位路徑是否與這裡假設的一致，有落差就回來修這裡，
      不要在呼叫端做欄位名稱的特例修補。

用法（於 code/ 目錄下，通常由 pipeline.trigger.service 呼叫）：
    export CWA_API_KEY="CWA-你的授權碼"      # https://opendata.cwa.gov.tw 免費申請
    python -m pipeline.trigger.earthquake      # 僅供除錯：印出最近幾筆地震報告
"""

from __future__ import annotations

import os
import ssl
from datetime import datetime

from pipeline.trigger.thresholds import EarthquakeObservation, EarthquakeStation

CWA_EARTHQUAKE_URL = (
    "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001"
)


def _as_list(x):
    return [] if x is None else (x if isinstance(x, list) else [x])


def _session():
    """跟 pipeline.ingest.cwa 相同的 TLS 放寬設定（CWA 平台憑證鏈在部分
    環境會被 OpenSSL 3 的嚴格 X509 檢查擋下，這裡放寬到瀏覽器等級）。"""
    import requests
    from requests.adapters import HTTPAdapter

    class _TLS(HTTPAdapter):
        def init_poolmanager(self, *a, **k):
            ctx = ssl.create_default_context()
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            k["ssl_context"] = ctx
            return super().init_poolmanager(*a, **k)

    session = requests.Session()
    session.mount("https://", _TLS())
    return session


def _parse_time(s: str) -> datetime:
    # CWA 慣用格式 "2025-09-01 14:23:00"；偶爾帶 "T" 分隔，兩者都接受。
    s = (s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"無法解析地震時間格式：{s!r}")


def _first(d: dict, *keys, default=None):
    """大小寫不拘、取第一個命中的 key（CWA JSON 欄位命名偶有大小寫差異）。"""
    for k in keys:
        for actual in d:
            if actual.lower() == k.lower():
                return d[actual]
    return default


def fetch_significant_earthquakes(api_key: str, limit: int = 10,
                                   timeout: int = 30) -> list[EarthquakeObservation]:
    """抓最近 `limit` 筆顯著有感地震報告，回傳依時間新到舊排序的觀測清單。

    每筆報告的測站震度明細取自報告本身附的 `Intensity.ShakingArea`；
    CWA 沒有附測站海拔，故 EarthquakeStation.elevation_m 一律是 None
    （thresholds.is_mountain_station 會自動退回經緯度概略判斷）。
    """
    import requests

    session = _session()
    resp = session.get(
        CWA_EARTHQUAKE_URL,
        params={"Authorization": api_key, "format": "JSON", "limit": limit},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    out: list[EarthquakeObservation] = []
    records = (data.get("records") or {})
    for eq in _as_list(records.get("Earthquake") or records.get("earthquake")):
        try:
            info = _first(eq, "EarthquakeInfo", default={})
            epi = _first(info, "Epicenter", default={})
            mag = _first(info, "EarthquakeMagnitude", default={})

            magnitude = float(_first(mag, "MagnitudeValue", default="nan"))
            epi_lat = float(_first(epi, "EpicenterLatitude", default="nan"))
            epi_lon = float(_first(epi, "EpicenterLongitude", default="nan"))
            origin_time = _parse_time(_first(info, "OriginTime", default=""))

            stations: list[EarthquakeStation] = []
            intensity_block = _first(eq, "Intensity", default={})
            for area in _as_list(_first(intensity_block, "ShakingArea", default=[])):
                for st in _as_list(_first(area, "EqStation", default=[])):
                    try:
                        stations.append(EarthquakeStation(
                            name=str(_first(st, "StationName", default="")),
                            lat=float(_first(st, "StationLatitude", default="nan")),
                            lon=float(_first(st, "StationLongitude", default="nan")),
                            intensity=str(_first(st, "SeismicIntensity",
                                                  "AreaIntensity", default="0")),
                        ))
                    except (TypeError, ValueError):
                        continue

            out.append(EarthquakeObservation(
                time=origin_time,
                magnitude=magnitude,
                epicenter_lat=epi_lat,
                epicenter_lon=epi_lon,
                stations=stations,
                source_id=str(_first(eq, "EarthquakeNo", "Web", default="")) or None,
            ))
        except (TypeError, ValueError, KeyError):
            continue  # 單筆解析失敗不該讓整批掛掉，跳過即可

    out.sort(key=lambda q: q.time, reverse=True)
    return out


def cli() -> None:
    api_key = os.environ.get("CWA_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("✗ 未設定 CWA_API_KEY，無法測試連線。")
    quakes = fetch_significant_earthquakes(api_key)
    print(f"✓ 取得 {len(quakes)} 筆顯著有感地震報告（E-A0015-001）")
    for q in quakes[:5]:
        print(f"  {q.time}｜規模 {q.magnitude}｜"
              f"({q.epicenter_lat:.2f}, {q.epicenter_lon:.2f})｜"
              f"{len(q.stations)} 個測站有震度紀錄")


if __name__ == "__main__":
    cli()
