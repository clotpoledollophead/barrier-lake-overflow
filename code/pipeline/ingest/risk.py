#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
風險模型 → 前端 risk.js
========================================================================
模型公式、係數、平均值三者直接採用「package」（隊友給的無 server
demo 包，`make_risk_snapshot.py`）裡的版本，寫死在本檔（見下方
LOGIT_INTERCEPT / LOGIT_COEF_RAW / FEATURE_MEANS），不再讀
`data/raw/risk/risk_formula_coefs.csv`——那是另一條（外部）建模
流程的輸出快照，本檔改採用 package 這份固定下來的公式做為唯一依據。

雨量特徵的取得方式也採用 package 的做法：抓 CWA O-A0002-001
自動雨量站的即時觀測，每座湖配最近的測站；沒有 CWA_API_KEY
或指定 --offline 時，用公式訓練時的平均值佔位（不是真雨量，
只用來確認串接正常）。

跟 package 原版不同的地方，只在於「湖泊清單從哪來」與
「輸出格式」，兩者都改成沿用本專案既有的東西，維持前端
（index.html / app.js / cap.js）完全不用改：
    * 湖泊清單：直接讀本專案已產生的 `dashboard/data/lakes.js`
      （window.BARRIER_LAKES），不需要 package 那個中繼的
      `lakes_static.json` / `make_lakes_static.py` 步驟。
    * 輸出：沿用本專案原本的 `window.LAKE_RISK` / 
      `window.RISK_MODEL_META` 格式與檔案位置
      （dashboard/data/risk.js），CAP 示警、證據卡等前端邏輯
      不用動。

已停用（不再被本檔讀取，但保留在磁碟上供對照／歸檔）：
    data/raw/risk/lake_risk_predictions.csv
    data/raw/risk/risk_formula_coefs.csv
若確定不需要，之後可以自行刪除；本檔不會自動動它們。

用法（於 code/ 目錄下）：
    export CWA_API_KEY="CWA-你的授權碼"     # opendata.cwa.gov.tw 免費申請
    python -m pipeline.ingest.risk                       # 即時
    python -m pipeline.ingest.risk --offline              # 無金鑰／先測流程
    python -m pipeline.ingest.risk --lakes dashboard/data/lakes.js \
        --out dashboard/data/risk.js
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys

from pipeline.ingest import cwa

DEFAULT_LAKES = "dashboard/data/lakes.js"
DEFAULT_OUT = "dashboard/data/risk.js"

# ── ERA5 訓練的 Logistic 公式（原始尺度，直接代未標準化雨量）──
# 數字照抄自 package 的 make_risk_snapshot.py，係數已固化，不用碰。
LOGIT_INTERCEPT = -3.0259088850754257
LOGIT_COEF_RAW = {
    "rain_7d": 0.03584177519291969, "rain_30d": -0.004287918499608312,
    "formed_by_rain": 0.8962255211119667, "rain_3d": -0.009245420542333044,
    "formed_by_quake": 0.7759403879983273, "volume": -0.327464903294034,
    "rain_1d": 0.007929487298660217,
}
FEATURE_MEANS = {
    "rain_7d": 51.235, "rain_30d": 218.283, "formed_by_rain": 0.584,
    "rain_3d": 21.968, "formed_by_quake": 0.264, "volume": 0.288,
    "rain_1d": 7.322,
}
N_POSITIVES = 12
DISCLAIMER = ("風險為模型推估，非現地實測；模型正例極少，"
              "泛化未驗證，僅供輔助判斷。")


def logit_prob(features: dict) -> float:
    z = LOGIT_INTERCEPT
    for key, coef in LOGIT_COEF_RAW.items():
        x = features.get(key)
        z += coef * (float(x) if x is not None else FEATURE_MEANS.get(key, 0.0))
    return 1.0 / (1.0 + math.exp(-z))


def zh_level(p: float) -> str:
    return "高" if p >= 0.5 else "低"


def alert_level(p: float) -> str:
    if p >= 0.80:
        return "IMMEDIATE"
    if p >= 0.50:
        return "URGENT"
    if p >= 0.30:
        return "WATCH"
    return "STABLE"


# ── 讀清冊（沿用本專案已產生的 lakes.js，不需另一份湖泊清單檔）──

def load_lakes(lakes_js_path: str) -> list[dict]:
    """從 dashboard/data/lakes.js 取出全部湖泊，轉成套公式需要的特徵。

    causeKey 已由 pipeline.ingest.inventory 標好（rain/quake），
    不需要像 package 原版那樣用文字比對猜測誘因。
    volume 清冊單位是萬 m³，公式要的是百萬 m³ → 除以 100
    （與 package 的 make_lakes_static.py 相同換算）。
    """
    with open(lakes_js_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"window\.BARRIER_LAKES\s*=\s*(\[.*?\]);", text, re.S)
    if not m:
        raise SystemExit(f"✗ 在 {lakes_js_path} 找不到 window.BARRIER_LAKES = [...]")
    lakes = json.loads(m.group(1))

    out = []
    for lk in lakes:
        name, lat, lon = lk.get("name"), lk.get("lat"), lk.get("lon")
        if not name or lat is None or lon is None:
            continue
        vol_wan = lk.get("volume")
        out.append({
            "name": name,
            "lat": float(lat),
            "lon": float(lon),
            "volume": (float(vol_wan) / 100.0) if vol_wan not in (None, "") else None,
            "formed_by_rain": 1 if lk.get("causeKey") == "rain" else 0,
            "formed_by_quake": 1 if lk.get("causeKey") == "quake" else 0,
        })
    return out


def write_risk_js(path: str, risk: dict, meta: dict) -> None:
    body = json.dumps(risk, ensure_ascii=False, indent=1)
    meta_body = json.dumps(meta, ensure_ascii=False, indent=1)
    out = (
        "/* 由 pipeline/ingest/risk.py 產生，請勿手動編輯。\n"
        "   模型與係數採用 package（make_risk_snapshot.py）版本，\n"
        "   雨量特徵來自 CWA O-A0002-001 即時觀測（或 offline 平均值佔位，\n"
        "   見 RISK_MODEL_META.mode）。\n"
        "   注意：n_positives 極少（見 RISK_MODEL_META.nPositives），\n"
        "   模型驗證方式與泛化能力尚待確認，前端須如實揭露此限制，\n"
        "   不可把風險分數當成已驗證的預測值呈現。 */\n\n"
        f"window.LAKE_RISK = {body};\n\n"
        f"window.RISK_MODEL_META = {meta_body};\n"
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)


def main(lakes_path: str, out_path: str, offline: bool = False) -> None:
    lakes = load_lakes(lakes_path)

    api_key = os.environ.get("CWA_API_KEY", "").strip()
    stations = None
    if not offline and api_key:
        print("抓取 CWA 即時雨量 O-A0002-001 …")
        try:
            stations = cwa.fetch_rainfall_stations(api_key)
            print(f"  取得 {len(stations)} 個雨量站")
        except Exception as exc:  # 網路/憑證問題不應讓整條 pipeline 中斷
            print(f"⚠ 抓取 CWA 即時雨量失敗（{exc}），退回 offline 佔位")
            stations = None
    elif not offline:
        print("⚠ 未設定 CWA_API_KEY → 退回 offline 佔位（僅測流程，非真雨量）")

    risk = {}
    n_hi = 0
    for lk in lakes:
        if stations:
            r24, dist_km = cwa.nearest_station_rain(lk["lat"], lk["lon"], stations)
            rain = {"rain_1d": r24, "rain_3d": r24, "rain_7d": r24,
                     "rain_30d": FEATURE_MEANS["rain_30d"]}
        else:
            rain = {k: FEATURE_MEANS[k] for k in
                     ("rain_1d", "rain_3d", "rain_7d", "rain_30d")}
            dist_km = None

        features = dict(rain)
        features["volume"] = lk["volume"] if lk["volume"] is not None else FEATURE_MEANS["volume"]
        features["formed_by_rain"] = lk["formed_by_rain"]
        features["formed_by_quake"] = lk["formed_by_quake"]

        p = logit_prob(features)
        if p >= 0.5:
            n_hi += 1
        risk[lk["name"]] = {
            "date": dt.date.today().isoformat(),
            "rain_1d": rain["rain_1d"], "rain_3d": rain["rain_3d"],
            "rain_7d": rain["rain_7d"], "rain_30d": rain["rain_30d"],
            "volume": features["volume"],
            "formed_by_quake": features["formed_by_quake"],
            "formed_by_rain": features["formed_by_rain"],
            "nearest_station_km": dist_km,
            "risk_prob": round(p, 4),
            "risk_level": zh_level(p),
            "alert": alert_level(p),
        }

    meta = {
        "model": "Logistic (package: make_risk_snapshot.py)",
        "mode": "LIVE(CWA O-A0002-001)" if stations else "OFFLINE(訓練平均值佔位)",
        "updated": dt.datetime.now().isoformat(),
        "nPositives": N_POSITIVES,
        "rocAuc": None,
        "coefficients": {"intercept": LOGIT_INTERCEPT, **LOGIT_COEF_RAW},
        "disclaimer": DISCLAIMER,
    }

    write_risk_js(out_path, risk, meta)

    print(f"✓ 已寫入 {out_path}｜{len(risk)} 座湖｜判高 {n_hi}｜"
          f"{'即時' if stations else 'offline'}")


def cli() -> None:
    ap = argparse.ArgumentParser(
        description="用 package 的模型與 CWA 即時雨量產生 risk.js")
    ap.add_argument("--lakes", default=DEFAULT_LAKES)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--offline", action="store_true",
                     help="不呼叫 CWA API，用訓練平均值佔位（僅測流程）")
    args = ap.parse_args()
    main(args.lakes, args.out, offline=args.offline)


if __name__ == "__main__":
    cli()
