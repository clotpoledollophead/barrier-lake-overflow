#!/usr/bin/env python3
"""
test_assess_pipeline.py — pipeline.assess.synthetic_dem / pipeline.assess.run 單元測試

只測合成地形分支（不需要真實 DEM 檔案）。真實 DEM 分支
（`_assess_with_real_dem`）需要真的 GeoTIFF，不在本檔測試範圍——
跟 `test_hypsometry`/`test_inundation` 對真實檔案 I/O 函式的處理方式一致
（那些函式本身已經在 `test_assess.py` 測過核心演算法，本檔只測
「接起來之後，給一筆湖泊紀錄能不能吐出合理結果」這件事）。

執行（於 code/ 目錄下）：
    pytest tests/test_assess_pipeline.py -v
"""

from __future__ import annotations

import unittest

from pipeline.assess import run as R
from pipeline.assess.synthetic_dem import (
    build_synthetic_valley_dem,
    grow_until_capacity,
    hash_azimuth_deg,
)

LAKE_LARGE = {"id": "bl071", "name": "花蓮馬太鞍溪", "lat": 23.70061,
              "lon": 121.29752, "volume": 9100.0, "statusKey": "watch"}
LAKE_SMALL = {"id": "bl074", "name": "加走寮溪", "lat": 23.66271,
              "lon": 120.69867, "volume": 10.0, "statusKey": "watch"}
LAKE_ZERO_VOLUME = {"id": "bl075", "name": "花蓮萬里溪", "lat": 23.79204,
                     "lon": 121.26957, "volume": 0.0, "statusKey": "watch"}
LAKE_STABLE = {"id": "bl999", "name": "已穩定湖", "lat": 24.0,
               "lon": 121.0, "volume": 5.0, "statusKey": "stable"}


class TestSyntheticDem(unittest.TestCase):
    def test_azimuth_is_deterministic(self):
        self.assertEqual(hash_azimuth_deg("bl071"), hash_azimuth_deg("bl071"))
        self.assertTrue(0 <= hash_azimuth_deg("bl071") < 360)

    def test_azimuth_varies_by_lake_id(self):
        # 不強求完全不同，但至少不是所有湖都一樣（demo 視覺多樣性）
        azimuths = {hash_azimuth_deg(f"bl{i:03d}") for i in range(10)}
        self.assertGreater(len(azimuths), 1)

    def test_dem_shape_matches_request(self):
        r = build_synthetic_valley_dem("bl074", 23.66271, 120.69867,
                                        n_rows=120, n_cols=60)
        self.assertEqual(r.dem.shape, (120, 60))

    def test_ridge_is_local_maximum_at_dam_row(self):
        r = build_synthetic_valley_dem("bl074", 23.66271, 120.69867,
                                        n_rows=120, n_cols=60)
        self.assertGreater(r.ridge_crest_el, r.dem[r.upstream_pour_rc])
        self.assertGreater(r.ridge_crest_el, r.dem[r.downstream_pour_rc])

    def test_rowcol_to_lonlat_dam_site_matches_origin(self):
        r = build_synthetic_valley_dem("bl074", 23.66271, 120.69867,
                                        n_rows=120, n_cols=60)
        dam_row = (r.upstream_pour_rc[0] + r.downstream_pour_rc[0]) // 2
        dam_col = r.dem.shape[1] // 2
        lon, lat = r.rowcol_to_lonlat(dam_row, dam_col)
        self.assertAlmostEqual(lon, 120.69867, places=3)
        self.assertAlmostEqual(lat, 23.66271, places=3)

    def test_grow_until_capacity_reaches_target(self):
        _result, curve = grow_until_capacity("bl071", 23.70061, 121.29752, 9100.0)
        self.assertGreaterEqual(curve[-1][1], 9100.0)

    def test_grow_until_capacity_stops_at_safety_cap_for_absurd_volume(self):
        # 極端大的目標容積不該讓函式無限迴圈，應該在 MAX_N_ROWS 停下來
        result, _curve = grow_until_capacity("bl999", 24.0, 121.0, 10_000_000.0)
        self.assertLessEqual(result.dem.shape[0], 3600)


class TestAssessRun(unittest.TestCase):
    def test_interp_elevation_within_range(self):
        curve = [(100.0, 0.0), (105.0, 10.0), (110.0, 30.0)]
        el = R._interp_elevation_for_volume(curve, 20.0)
        self.assertAlmostEqual(el, 107.5)

    def test_interp_elevation_below_range_returns_first(self):
        curve = [(100.0, 0.0), (105.0, 10.0)]
        self.assertEqual(R._interp_elevation_for_volume(curve, -5.0), 100.0)

    def test_interp_elevation_above_range_returns_last(self):
        curve = [(100.0, 0.0), (105.0, 10.0)]
        self.assertEqual(R._interp_elevation_for_volume(curve, 999.0), 105.0)

    def test_synthetic_assessment_produces_polygon(self):
        result = R.assess_lake(LAKE_LARGE, dem_dir="/nonexistent", allow_synthetic=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.method, "synthetic_demo_dem")
        self.assertGreater(len(result.polygon), 2)
        self.assertIsNotNone(result.disclaimer)

    def test_no_real_dem_and_synthetic_disabled_returns_none(self):
        result = R.assess_lake(LAKE_LARGE, dem_dir="/nonexistent", allow_synthetic=False)
        self.assertIsNone(result)

    def test_dam_height_scales_with_volume(self):
        small = R.assess_lake(LAKE_SMALL, dem_dir="/nonexistent", allow_synthetic=True)
        large = R.assess_lake(LAKE_LARGE, dem_dir="/nonexistent", allow_synthetic=True)
        self.assertLess(small.dam_height_m, large.dam_height_m)

    def test_zero_volume_lake_still_produces_a_result(self):
        result = R.assess_lake(LAKE_ZERO_VOLUME, dem_dir="/nonexistent", allow_synthetic=True)
        self.assertIsNotNone(result)
        self.assertGreater(result.dam_height_m, 0.0)

    def test_disclaimer_present_only_for_synthetic(self):
        result = R.assess_lake(LAKE_SMALL, dem_dir="/nonexistent", allow_synthetic=True)
        self.assertIn("合成", result.disclaimer)

    def test_to_dict_is_json_serializable(self):
        import json
        result = R.assess_lake(LAKE_SMALL, dem_dir="/nonexistent", allow_synthetic=True)
        json.dumps(result.to_dict(), ensure_ascii=False)  # 不該丟例外

    def test_assess_watch_lakes_skips_non_watch_status(self):
        results = R.assess_watch_lakes(
            [LAKE_SMALL, LAKE_STABLE], dem_dir="/nonexistent", allow_synthetic=True)
        self.assertEqual({r.lake_id for r in results}, {LAKE_SMALL["id"]})

    def test_assess_watch_lakes_skips_when_synthetic_disabled(self):
        results = R.assess_watch_lakes(
            [LAKE_SMALL], dem_dir="/nonexistent", allow_synthetic=False)
        self.assertEqual(results, [])

    def test_write_inundation_js_roundtrip(self):
        import json
        import os
        import re
        import tempfile

        results = R.assess_watch_lakes(
            [LAKE_SMALL, LAKE_LARGE], dem_dir="/nonexistent", allow_synthetic=True)
        with tempfile.TemporaryDirectory() as d:
            out_path = os.path.join(d, "inundation.js")
            R.write_inundation_js(out_path, results)
            text = open(out_path, encoding="utf-8").read()
            m = re.search(r"window\.LAKE_INUNDATION\s*=\s*(\{.*\});\s*$", text, re.S)
            data = json.loads(m.group(1))
            self.assertEqual(set(data.keys()), {LAKE_SMALL["id"], LAKE_LARGE["id"]})
            for entry in data.values():
                self.assertEqual(entry["method"], "synthetic_demo_dem")
                self.assertIn("disclaimer", entry)


if __name__ == "__main__":
    unittest.main()
