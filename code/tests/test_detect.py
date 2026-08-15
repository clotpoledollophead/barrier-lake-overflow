#!/usr/bin/env python3
"""
test_detect.py — pipeline.detect 單元測試（純邏輯，不需真實影像）

執行（於 code/ 目錄下）：
    pytest tests/test_detect.py -v
"""

from __future__ import annotations

import unittest

import numpy as np

from pipeline.detect import water as W
from pipeline.detect import landslide as L
from pipeline.detect import barrier_lake as B


class TestWater(unittest.TestCase):
    def test_sar_otsu_separates_two_populations(self):
        rng = np.random.default_rng(0)
        land = rng.normal(-8, 1.0, (30, 30))
        sigma0 = land.copy()
        sigma0[:8, :8] = rng.normal(-22, 1.0, (8, 8))
        mask = W.water_from_sar(sigma0)
        self.assertTrue(mask[3, 3])
        self.assertFalse(mask[20, 20])

    def test_ndwi_thresholds_correctly(self):
        green = np.array([[0.4, 0.1]])
        nir = np.array([[0.05, 0.4]])
        water = W.water_from_optical(green, nir)
        self.assertTrue(water[0, 0])
        self.assertFalse(water[0, 1])

    def test_fuse_union_and_intersection(self):
        a = np.array([[True, False], [True, False]])
        b = np.array([[True, True], [False, False]])
        out = W.fuse(a, b)
        self.assertTrue(out["union"][0, 1])
        self.assertFalse(out["intersection"][0, 1])
        self.assertTrue(out["intersection"][0, 0])
        self.assertEqual(out["n_tracks"], 2)

    def test_fuse_single_track_has_no_intersection(self):
        a = np.array([[True, False]])
        out = W.fuse(a, None)
        self.assertIsNone(out["intersection"])
        self.assertEqual(out["n_tracks"], 1)

    def test_clean_mask_removes_small_objects(self):
        m = np.zeros((20, 20), dtype=bool)
        m[0, 0] = True
        m[5:15, 5:15] = True
        out = W.clean_mask(m, cellsize=10.0, min_area_ha=0.5)
        self.assertFalse(out[0, 0])
        self.assertTrue(out[10, 10])


class TestLandslide(unittest.TestCase):
    def test_amplitude_change_detects_delta(self):
        pre = np.full((3, 3), -10.0)
        post = pre.copy()
        post[1, 1] = -2.0
        mask = L.landslide_from_amplitude(pre, post, threshold_db=3.0)
        self.assertTrue(mask[1, 1])
        self.assertFalse(mask[0, 0])

    def test_coherence_drop_detects_change(self):
        pre = np.full((3, 3), 0.8)
        post = pre.copy()
        post[0, 0] = 0.1
        mask = L.landslide_from_coherence(pre, post, drop_threshold=0.3)
        self.assertTrue(mask[0, 0])
        self.assertFalse(mask[1, 1])

    def test_slope_filter_excludes_flat_areas(self):
        mask = np.array([[True, True]])
        slope = np.array([[2.0, 30.0]])
        out = L.clean_by_slope(mask, slope, min_slope_deg=15.0)
        self.assertFalse(out[0, 0])
        self.assertTrue(out[0, 1])


class TestBarrierLake(unittest.TestCase):
    def setUp(self):
        self.shape = (20, 20)
        self.water = np.zeros(self.shape, dtype=bool)
        self.water[5:10, 5:10] = True
        self.river = np.zeros(self.shape, dtype=bool)
        self.river[:, 7] = True
        self.landslide = np.zeros(self.shape, dtype=bool)
        self.landslide[0:4, 5:10] = True

    def test_classifies_grade_a_with_optical_confirmation(self):
        intersection = self.water.copy()
        result = B.classify(self.water, intersection, self.river, self.landslide)
        self.assertEqual(result[0].confidence, "A")
        self.assertTrue(result[0].actionable)

    def test_classifies_grade_b_with_sar_persistence(self):
        persist = np.zeros(self.shape, dtype=int)
        persist[5:10, 5:10] = 3
        result = B.classify(self.water, None, self.river, self.landslide,
                            persist_count_map=persist, min_persist_passes=2)
        self.assertEqual(result[0].confidence, "B")
        self.assertTrue(result[0].actionable)

    def test_classifies_grade_c_without_river_or_slide(self):
        empty_river = np.zeros(self.shape, dtype=bool)
        empty_slide = np.zeros(self.shape, dtype=bool)
        result = B.classify(self.water, None, empty_river, empty_slide)
        self.assertEqual(result[0].confidence, "C")
        self.assertFalse(result[0].actionable)


if __name__ == "__main__":
    unittest.main()
