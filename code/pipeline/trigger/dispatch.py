#!/usr/bin/env python3
"""
dispatch.py — 影像調度（架構文件 §02 FIG.1「影像調度」、§04「任務建立後
自動向 CDSE 查詢：事件後第一景可用的 Sentinel-1...以及事件前最近一景
無雲基準影像"）。

Copernicus Data Space Ecosystem（CDSE）用 OAuth2 client-credentials 認證 +
STAC API 查詢，跟本專案其他外部 API（CWA）介接方式類似，一樣採「拿不到
就降級、不讓管線掛掉」的原則：

    get_access_token()   —— 用 CDSE_CLIENT_ID / CDSE_CLIENT_SECRET 換 token；
                             CDSE 帳號本身免費申請（見 dataspace.copernicus.eu），
                             但 client credentials 是額外要開的 OAuth client，
                             沒設定時所有查詢函式回傳 None，不丟例外。
    query_latest_scene()  —— 查 AOI 內、指定時間之前最新一景 Sentinel-1 IW GRD。
    query_next_scene()    —— 查 AOI 內、指定時間之後最早一景（已排程但尚未
                             拍攝的未來過境，供指揮人員預期「下次衛星幾點經過」）。
    estimate_next_pass()  —— 上面兩個查詢都失敗（無網路/無憑證/查詢範圍內
                             真的沒有排程資料）時的最後備援：純粹用台灣約
                             6 天重訪週期的經驗值往後推算，明確標註為估計值，
                             不是真正的軌道預報。

已知限制：
    · STAC 查詢的 collection/query 語法是依 CDSE 官方文件公開格式撰寫，
      跟 pipeline.trigger.earthquake 一樣，本專案環境沒有對外網路可以在
      CI 裡實測，第一次接上真帳號時務必核對回應格式。
    · estimate_next_pass() 只是經驗值外推，不是真正的軌道預報；
      要做到文件寫的「已排程的未來過境時刻」，需要查 CDSE 的
      acquisition plan（或用 orbit TLE + skyfield 自己算），
      這是輔導期可以升級的項目，不是本檔的目標。

用法（於 code/ 目錄下）：
    export CDSE_CLIENT_ID="..."
    export CDSE_CLIENT_SECRET="..."
    python -m pipeline.trigger.dispatch --lat 23.67 --lon 121.42 --radius-km 30
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from typing import Optional

CDSE_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
CDSE_STAC_SEARCH_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"

SENTINEL1_REVISIT_DAYS = 6.0  # 台灣約每 6 天覆蓋（架構文件 §03 TABLE 1）


def get_access_token(client_id: Optional[str] = None,
                      client_secret: Optional[str] = None,
                      timeout: int = 15) -> Optional[str]:
    """用 CDSE client credentials 換 access token；缺憑證或連線失敗都回傳
    None（不丟例外），呼叫端應該把 None 當成「這次沒有影像調度能力，
    先用 estimate_next_pass() 頂著」處理，而不是讓整條觸發流程失敗。
    """
    client_id = client_id or os.environ.get("CDSE_CLIENT_ID", "").strip()
    client_secret = client_secret or os.environ.get("CDSE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    try:
        import requests
        resp = requests.post(
            CDSE_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception:
        return None


def _bbox_from_circle(lat: float, lon: float, radius_km: float) -> list[float]:
    """粗略 bbox（跟 catchment.polygon_ring 同樣的緯度換算近似）。"""
    import math
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 1e-6))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def _stac_search(token: Optional[str], bbox: list[float], datetime_range: str,
                  limit: int = 5, timeout: int = 30) -> Optional[list[dict]]:
    if token is None:
        return None
    try:
        import requests
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "collections": ["SENTINEL-1"],
            "bbox": bbox,
            "datetime": datetime_range,
            "query": {"productType": {"eq": "IW_GRDH_1S"}},
            "limit": limit,
            "sortby": [{"field": "datetime", "direction": "desc"}],
        }
        resp = requests.post(CDSE_STAC_SEARCH_URL, json=body, headers=headers,
                              timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("features", [])
    except Exception:
        return None


def query_latest_scene(lat: float, lon: float, radius_km: float,
                        before: datetime, token: Optional[str] = None
                        ) -> Optional[dict]:
    """查 AOI 內、`before` 之前最新一景 Sentinel-1 GRD（事件前無雲基準影像）。
    查不到（無憑證/無網路/範圍內真的沒有資料）回傳 None。
    """
    bbox = _bbox_from_circle(lat, lon, radius_km)
    date_range = f"../{before.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    features = _stac_search(token, bbox, date_range)
    return features[0] if features else None


def query_next_scene(lat: float, lon: float, radius_km: float,
                      after: datetime, token: Optional[str] = None
                      ) -> Optional[dict]:
    """查 AOI 內、`after` 之後最早一景（含已排程但尚未拍攝的未來過境）。"""
    bbox = _bbox_from_circle(lat, lon, radius_km)
    date_range = f"{after.strftime('%Y-%m-%dT%H:%M:%SZ')}/.."
    features = _stac_search(token, bbox, date_range)
    return features[-1] if features else None


def estimate_next_pass(after: datetime,
                        last_known_pass: Optional[datetime] = None,
                        revisit_days: float = SENTINEL1_REVISIT_DAYS) -> dict:
    """query_next_scene() 拿不到結果時的最後備援：純粹用重訪週期外推，
    回傳值明確標註 estimated=True，前端／報告絕不可跟真實過境時刻混用。

    >>> from datetime import datetime
    >>> r = estimate_next_pass(datetime(2025, 9, 1))
    >>> r["estimated"]
    True
    """
    base = last_known_pass or after
    return {
        "estimated": True,
        "next_pass_eta": base + timedelta(days=revisit_days),
        "basis": f"經驗重訪週期 {revisit_days} 天外推，非真實軌道預報",
    }


def cli() -> None:
    ap = argparse.ArgumentParser(description="查詢 AOI 內可用的 Sentinel-1 影像")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius-km", type=float, default=30.0)
    args = ap.parse_args()

    token = get_access_token()
    now = datetime.utcnow()
    if token is None:
        print("⚠ 未設定 CDSE_CLIENT_ID/CDSE_CLIENT_SECRET（或換 token 失敗），"
              "改用重訪週期估計下次過境：")
        print(f"  {estimate_next_pass(now)}")
        return

    latest = query_latest_scene(args.lat, args.lon, args.radius_km, now, token)
    nxt = query_next_scene(args.lat, args.lon, args.radius_km, now, token)
    print(f"事件前最近一景：{latest or '（查無資料）'}")
    print(f"下一景（含已排程未來過境）：{nxt or estimate_next_pass(now)}")


if __name__ == "__main__":
    cli()
