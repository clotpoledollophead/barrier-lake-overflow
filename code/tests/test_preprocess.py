#!/usr/bin/env python3
"""
test_preprocess.py — pipeline.preprocess 單元測試

只測不需要真實衛星資料的核心邏輯：地形量測公式、遮罩幾何、
輻射校正公式。檔案 I/O（rasterio 讀寫）與 SNAP 整合不在本檔測試範圍。

執行（於 code/ 目錄下）：
    pytest tests/test_preprocess.py -v
"""

from __future__ import annotations

import unittest

import numpy as np

from pipeline.preprocess import mask as M
from pipeline.preprocess import sar as S


class TestSlopeAspect(unittest.TestCase):
    def test_flat_dem_has_zero_slope(self):
        dem = np.full((5, 5), 100.0)
        slope, _ = M.slope_aspect(dem, cellsize=10.0)
        self.assertTrue(np.allclose(slope, 0.0))

    def test_slope_mask_flags_steep_cliff(self):
        dem = np.zeros((5, 5))
        dem[:, 3:] = 1000.0  # 近乎垂直崖壁
        m = M.slope_mask(dem, cellsize=10.0, max_slope_deg=45.0)
        self.assertTrue(m[2, 2] or m[2, 3])


class TestLayoverShadow(unittest.TestCase):
    def test_flat_terrain_not_flagged(self):
        dem = np.full((10, 10), 500.0)
        geom = M.SarGeometry(incidence_deg=35.0, heading_deg=350.0)
        m = M.layover_shadow_mask(dem, cellsize=10.0, geom=geom)
        self.assertFalse(m.any())

    def test_steep_facing_slope_is_layover(self):
        # 往北飛（heading=0）、右視 → 視線朝東；地形東側陡升（上坡方向朝東，
        # 與視線方向一致）→ 局部入射角應大幅小於場景入射角，判為疊掩
        dem = np.zeros((10, 10))
        for c in range(10):
            dem[:, c] = c * 50.0
        geom = M.SarGeometry(incidence_deg=35.0, heading_deg=0.0)
        m = M.layover_shadow_mask(dem, cellsize=10.0, geom=geom,
                                  layover_max_deg=40.0, shadow_min_deg=999.0)
        self.assertTrue(m[5, 5])


class TestCalibration(unittest.TestCase):
    def test_calibrate_monotonic_with_dn(self):
        dn = np.array([[10.0, 100.0, 1000.0]])
        out = S.calibrate(dn)
        self.assertTrue(out[0, 0] < out[0, 1] < out[0, 2])

    def test_calibrate_linear_matches_db(self):
        dn = np.array([[500.0]])
        db = S.calibrate(dn, to_db=True)
        lin = S.calibrate(dn, to_db=False)
        self.assertAlmostEqual(10.0 * np.log10(lin[0, 0]), db[0, 0], places=6)

    def test_despeckle_removes_impulse_noise(self):
        a = np.ones((5, 5))
        a[2, 2] = 500.0
        out = S.despeckle(a, size=3)
        self.assertLess(out[2, 2], 500.0)


if __name__ == "__main__":
    unittest.main()
