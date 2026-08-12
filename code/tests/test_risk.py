#!/usr/bin/env python3
"""
test_risk.py — pipeline.ingest.risk 單元測試（package 模型版）

只測不連網的部分：logit 公式是否與 package 的 make_risk_snapshot.py
數字一致、lakes.js 解析與單位換算是否正確、offline 模式下整條流程
能否正常寫出 risk.js。CWA 連線（pipeline.ingest.cwa.fetch_rainfall_stations）
不在本檔測試範圍內——那需要真的網路與金鑰。

執行（於 code/ 目錄下）：
    pytest tests/test_risk.py -v
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import unittest

from pipeline.ingest import risk as R

FIXTURE_LAKES_JS = (
    "/* 測試用 fixture，格式比照 pipeline.ingest.inventory 的輸出 */\n"
    "window.BARRIER_LAKES = [\n"
    ' {"name": "A湖", "lat": 23.9, "lon": 121.4, "volume": 43.0,'
    ' "causeKey": "quake", "statusKey": "watch"},\n'
    ' {"name": "B湖", "lat": 24.0, "lon": 121.5, "volume": 10.0,'
    ' "causeKey": "rain", "statusKey": "gone"},\n'
    ' {"name": "C湖", "lat": 24.1, "lon": 121.6, "volume": null,'
    ' "causeKey": "", "statusKey": "watch"}\n'
    "];\n"
)


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestFormulaMatchesPackage(unittest.TestCase):
    """公式數字必須跟 package 的 make_risk_snapshot.py 完全一致
    （這正是這次改動的目的：模型與係數全部改用 package 那份）。"""

    def test_constants(self):
        self.assertAlmostEqual(R.LOGIT_INTERCEPT, -3.0259088850754257)
        self.assertAlmostEqual(R.LOGIT_COEF_RAW["rain_7d"], 0.03584177519291969)
        self.assertAlmostEqual(R.LOGIT_COEF_RAW["volume"], -0.327464903294034)
        self.assertAlmostEqual(R.FEATURE_MEANS["rain_30d"], 218.283)
        self.assertEqual(R.N_POSITIVES, 12)

    def test_logit_prob_hand_calc(self):
        features = {"rain_1d": 5.0, "rain_3d": 20.0, "rain_7d": 60.0,
                    "rain_30d": 300.0, "volume": 0.4,
                    "formed_by_rain": 0, "formed_by_quake": 1}
        z = R.LOGIT_INTERCEPT
        for k, c in R.LOGIT_COEF_RAW.items():
            z += c * features[k]
        expected = 1.0 / (1.0 + math.exp(-z))
        self.assertAlmostEqual(R.logit_prob(features), expected)

    def test_missing_feature_falls_back_to_training_mean(self):
        p_empty = R.logit_prob({})
        p_at_means = R.logit_prob(dict(R.FEATURE_MEANS))
        self.assertAlmostEqual(p_empty, p_at_means)


class TestLevels(unittest.TestCase):
    def test_zh_level_threshold(self):
        self.assertEqual(R.zh_level(0.49), "低")
        self.assertEqual(R.zh_level(0.5), "高")

    def test_alert_level_thresholds(self):
        self.assertEqual(R.alert_level(0.9), "IMMEDIATE")
        self.assertEqual(R.alert_level(0.6), "URGENT")
        self.assertEqual(R.alert_level(0.35), "WATCH")
        self.assertEqual(R.alert_level(0.1), "STABLE")


class TestLoadLakes(unittest.TestCase):
    def test_all_lakes_kept_regardless_of_status(self):
        # package 原版不分現況存續，一律計算；跟舊版 risk_live 不同，
        # 這裡不再依 statusKey 篩選。
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            lakes = R.load_lakes(lakes_path)
        self.assertEqual({lk["name"] for lk in lakes}, {"A湖", "B湖", "C湖"})

    def test_volume_unit_conversion_and_cause_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            lakes = R.load_lakes(lakes_path)
        a = next(lk for lk in lakes if lk["name"] == "A湖")
        self.assertAlmostEqual(a["volume"], 0.43)  # 43 萬 m³ → 0.43 百萬 m³
        self.assertEqual(a["formed_by_quake"], 1)
        self.assertEqual(a["formed_by_rain"], 0)

        c = next(lk for lk in lakes if lk["name"] == "C湖")
        self.assertIsNone(c["volume"])  # 缺蓄水量時保留 None，交給 main() 佔位


class TestOfflineEndToEnd(unittest.TestCase):
    def test_writes_valid_risk_js_for_every_lake(self):
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            out_path = os.path.join(d, "risk.js")

            R.main(lakes_path, out_path, offline=True)

            text = open(out_path, encoding="utf-8").read()
            risk = json.loads(
                re.search(r"window\.LAKE_RISK\s*=\s*(\{.*?\});\s*\n\s*window\.RISK_MODEL_META",
                          text, re.S).group(1))
            meta = json.loads(
                re.search(r"window\.RISK_MODEL_META\s*=\s*(\{.*\});\s*$", text, re.S).group(1))

        self.assertEqual(set(risk.keys()), {"A湖", "B湖", "C湖"})
        for entry in risk.values():
            self.assertIn(entry["risk_level"], ("高", "低"))
            self.assertIn("risk_prob", entry)
            self.assertIsNone(entry["nearest_station_km"])  # offline 模式沒有測站距離
        self.assertEqual(meta["mode"], "OFFLINE(訓練平均值佔位)")
        self.assertEqual(meta["nPositives"], 12)
        self.assertIsNone(meta["rocAuc"])


if __name__ == "__main__":
    unittest.main()
