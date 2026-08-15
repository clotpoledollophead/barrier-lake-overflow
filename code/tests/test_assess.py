#!/usr/bin/env python3
"""
test_assess.py — pipeline.assess 單元測試（純邏輯，合成 DEM，不需真實資料）

同時驗證 hypsometry.basin_curve 輸出格式與
pipeline.attribution.forecast.volume_at 相容（見 test_interop）。

執行（於 code/ 目錄下）：
    pytest tests/test_assess.py -v
"""

from __future__ import annotations

import unittest

import numpy as np

from pipeline.assess import hypsometry as H
from pipeline.assess import inundation as I
from pipeline.assess import exposure as E
from pipeline.attribution import forecast as F


def bowl_dem(size=15, floor=5.0, rim=50.0):
    """合成一個中間低、四周高的碗狀地形，模擬堵塞的山谷。"""
    yy, xx = np.mgrid[0:size, 0:size]
    center = size // 2
    dist = np.hypot(yy - center, xx - center)
    dem = floor + (dist / dist.max()) * (rim - floor)
    return dem


class TestHypsometry(unittest.TestCase):
    def test_curve_monotonic_increasing(self):
        dem = bowl_dem()
        curve = H.basin_curve(dem, cellsize=10.0, pour_point=(7, 7), crest_el=30.0)
        elevations = [e for e, _ in curve]
        volumes = [v for _, v in curve]
        self.assertEqual(elevations, sorted(elevations))
        self.assertEqual(volumes, sorted(volumes))

    def test_crest_el_limits_extent(self):
        dem = bowl_dem()
        curve = H.basin_curve(dem, cellsize=10.0, pour_point=(7, 7), crest_el=20.0)
        self.assertLessEqual(curve[-1][0], 20.0)

    def test_dam_height_and_crest_volume(self):
        dem = bowl_dem()
        curve = H.basin_curve(dem, cellsize=10.0, pour_point=(7, 7), crest_el=25.0)
        self.assertGreater(H.dam_height(curve), 0)
        self.assertGreater(H.volume_at_crest(curve), 0)

    def test_interop_with_attribution_forecast(self):
        """本模組輸出的曲線格式應能直接餵給 attribution.forecast。"""
        dem = bowl_dem()
        curve = H.basin_curve(dem, cellsize=10.0, pour_point=(7, 7), crest_el=30.0)
        vol = F.volume_at(curve, curve[len(curve) // 2][0])
        self.assertIsInstance(vol, float)
        self.assertGreaterEqual(vol, 0.0)


class TestInundation(unittest.TestCase):
    def test_extent_grows_with_water_level(self):
        dem = bowl_dem()
        low = I.bathtub_extent(dem, cellsize=10.0, pour_point=(7, 7), water_el=15.0)
        high = I.bathtub_extent(dem, cellsize=10.0, pour_point=(7, 7), water_el=25.0)
        self.assertLessEqual(low.sum(), high.sum())

    def test_no_flood_when_pour_point_above_water(self):
        dem = bowl_dem()
        mask = I.bathtub_extent(dem, cellsize=10.0, pour_point=(7, 7), water_el=0.0)
        self.assertFalse(mask.any())

    def test_max_depth_matches_water_level_minus_floor(self):
        dem = bowl_dem(floor=5.0)
        mask = I.bathtub_extent(dem, cellsize=10.0, pour_point=(7, 7), water_el=20.0)
        depth = I.max_depth(dem, mask, water_el=20.0)
        self.assertAlmostEqual(depth, 15.0, places=3)

    def test_max_distance_limits_extent(self):
        dem = np.full((5, 200), 5.0)  # 一條很長的平坦河道
        near = I.bathtub_extent(dem, cellsize=100.0, pour_point=(2, 0),
                                water_el=10.0, max_distance_km=0.5)
        far = I.bathtub_extent(dem, cellsize=100.0, pour_point=(2, 0),
                               water_el=10.0, max_distance_km=5.0)
        self.assertLess(near.sum(), far.sum())


class TestExposure(unittest.TestCase):
    def test_population_exposed_sums_only_flooded_cells(self):
        mask = np.array([[True, False], [True, True]])
        pop = np.array([[10.0, 5.0], [3.0, 2.0]])
        self.assertEqual(E.population_exposed(mask, pop), 15.0)

    def test_roads_interrupted_counts_intersection(self):
        inun = np.array([[True, True, False]])
        road = np.array([[True, False, True]])
        km = E.roads_interrupted_km(inun, road, cellsize=100.0)
        self.assertAlmostEqual(km, 0.1)

    def test_isolated_settlement_when_all_roads_cut(self):
        road = np.zeros((10, 10), dtype=bool)
        road[5, 3:8] = True
        inun = np.zeros((10, 10), dtype=bool)
        inun[5, 3:8] = True
        out = E.isolated_settlements([(5, 5)], road, inun, search_radius_px=3)
        self.assertTrue(out[0]["isolated"])

    def test_not_isolated_when_some_road_remains_dry(self):
        road = np.zeros((10, 10), dtype=bool)
        road[5, 3:8] = True
        inun = np.zeros((10, 10), dtype=bool)
        inun[5, 3:5] = True  # 只淹一部分
        out = E.isolated_settlements([(5, 5)], road, inun, search_radius_px=3)
        self.assertFalse(out[0]["isolated"])

    def test_summarize_bundles_all_metrics(self):
        mask = np.ones((5, 5), dtype=bool)
        pop = np.ones((5, 5))
        road = np.ones((5, 5), dtype=bool)
        result = E.summarize(mask, cellsize=10.0, population=pop, road_mask=road)
        self.assertIn("inundation_area_ha", result)
        self.assertIn("population_exposed", result)
        self.assertIn("roads_interrupted_km", result)


if __name__ == "__main__":
    unittest.main()
