#!/usr/bin/env python3
"""
test_forecast_run.py — pipeline.attribution.forecast_run 單元測試

重點測「集水面積未知時誠實回傳 None，不猜數字」這件事，以及跟
assess.run.AssessResult 串接後 forecast.py 本身的計算有沒有正確被呼叫到
（forecast.py 自己的計算邏輯已經在 test_attribution.py 測過，這裡不重測）。

執行（於 code/ 目錄下）：
    pytest tests/test_forecast_run.py -v
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import datetime

from pipeline.assess.run import AssessResult
from pipeline.attribution import forecast_run as FR

LAKE_KNOWN = {"id": "bl071", "name": "花蓮馬太鞍溪", "lat": 23.70061,
              "lon": 121.29752, "volume": 9100.0, "statusKey": "watch"}
LAKE_UNKNOWN = {"id": "bl074", "name": "加走寮溪", "lat": 23.66271,
                "lon": 120.69867, "volume": 10.0, "statusKey": "watch"}

CURVE = [(600.0, 0.0), (650.0, 5000.0), (700.0, 40000.0)]


def _assess_result(lake_id, lake_name, curve, crest_el, water_el=660.0, method="synthetic_demo_dem"):
    return AssessResult(
        lake_id=lake_id, lake_name=lake_name, method=method,
        area_ha=100.0, max_depth_m=10.0, dam_height_m=water_el - curve[0][0],
        volume_at_target_wan_m3=9100.0, water_elevation_m=water_el,
        polygon=[(121.0, 23.0), (121.01, 23.0), (121.0, 23.01)],
        hypsometric_curve=curve, crest_el=crest_el,
    )


class TestKnownCatchments(unittest.TestCase):
    def test_known_lake_has_citation(self):
        entry = FR.KNOWN_CATCHMENTS_KM2.get("bl071")
        self.assertIsNotNone(entry)
        self.assertGreater(entry["area_km2"], 0)
        self.assertIn("公頃", entry["source"])  # 來源文字裡帶著查證出處，不是空字串


class TestForecastForAssessResult(unittest.TestCase):
    def test_unknown_catchment_returns_none(self):
        r = _assess_result("bl074", "加走寮溪", CURVE, crest_el=700.0)
        fc = FR.forecast_for_assess_result(LAKE_UNKNOWN, r)
        self.assertIsNone(fc)

    def test_no_assess_result_returns_none(self):
        fc = FR.forecast_for_assess_result(LAKE_KNOWN, None)
        self.assertIsNone(fc)

    def test_assess_result_without_crest_el_returns_none(self):
        # real_dem 路徑目前 crest_el 一律是 None（見 assess/run.py）
        r = _assess_result("bl071", "花蓮馬太鞍溪", CURVE, crest_el=None, method="real_dem")
        fc = FR.forecast_for_assess_result(LAKE_KNOWN, r)
        self.assertIsNone(fc)

    def test_known_lake_with_curve_and_crest_produces_result(self):
        r = _assess_result("bl071", "花蓮馬太鞍溪", CURVE, crest_el=700.0, water_el=660.0)
        fc = FR.forecast_for_assess_result(LAKE_KNOWN, r, observed_at=datetime(2026, 8, 14, 12, 0))
        self.assertIsNotNone(fc)
        self.assertEqual(fc.catchment_km2, 63.23)
        self.assertAlmostEqual(fc.gap_m, 40.0)  # 700 - 660
        self.assertGreater(len(fc.narrative), 0)
        self.assertIn("合成", fc.disclaimer)  # method=synthetic_demo_dem 的額外揭露

    def test_disclaimer_mentions_catchment_is_verified(self):
        r = _assess_result("bl071", "花蓮馬太鞍溪", CURVE, crest_el=700.0)
        fc = FR.forecast_for_assess_result(LAKE_KNOWN, r)
        self.assertIn("查證過", fc.disclaimer)

    def test_to_dict_is_json_serializable(self):
        r = _assess_result("bl071", "花蓮馬太鞍溪", CURVE, crest_el=700.0)
        fc = FR.forecast_for_assess_result(LAKE_KNOWN, r)
        json.dumps(fc.to_dict(), ensure_ascii=False)  # 不該丟例外

    def test_high_rain_scenario_can_produce_overflow_estimate(self):
        # 水位已經很接近壩頂、給高雨量情境時，至少高情境應該估得出溢流時間
        near_crest_curve = [(600.0, 0.0), (698.0, 100.0), (700.0, 120.0)]
        r = _assess_result("bl071", "花蓮馬太鞍溪", near_crest_curve,
                            crest_el=700.0, water_el=699.0)
        fc = FR.forecast_for_assess_result(LAKE_KNOWN, r)
        self.assertTrue(fc.any_overflow)
        self.assertIsNotNone(fc.earliest)
        self.assertLessEqual(fc.earliest, fc.latest)


class TestForecastAllAndWrite(unittest.TestCase):
    def test_forecast_all_only_includes_known_catchments(self):
        results = {
            "bl071": _assess_result("bl071", "花蓮馬太鞍溪", CURVE, crest_el=700.0),
            "bl074": _assess_result("bl074", "加走寮溪", CURVE, crest_el=700.0),
        }
        fcs = FR.forecast_all([LAKE_KNOWN, LAKE_UNKNOWN], results)
        self.assertEqual([fc.lake_id for fc in fcs], ["bl071"])

    def test_write_forecast_js_empty_is_valid(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "forecast.js")
            FR.write_forecast_js(path, [])
            text = open(path, encoding="utf-8").read()
            m = re.search(r"window\.LAKE_FORECAST\s*=\s*(\{.*\});\s*$", text, re.S)
            self.assertEqual(json.loads(m.group(1)), {})

    def test_write_forecast_js_roundtrip(self):
        r = _assess_result("bl071", "花蓮馬太鞍溪", CURVE, crest_el=700.0)
        fc = FR.forecast_for_assess_result(LAKE_KNOWN, r)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "forecast.js")
            FR.write_forecast_js(path, [fc])
            text = open(path, encoding="utf-8").read()
            m = re.search(r"window\.LAKE_FORECAST\s*=\s*(\{.*\});\s*$", text, re.S)
            data = json.loads(m.group(1))
            self.assertEqual(set(data.keys()), {"bl071"})
            self.assertIn("narrative", data["bl071"])
            self.assertIn("catchmentSource", data["bl071"])


if __name__ == "__main__":
    unittest.main()
