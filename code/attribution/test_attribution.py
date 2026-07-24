#!/usr/bin/env python3
"""
test_attribution.py — 單元測試

用清冊裡 2025 年的三筆真實紀錄當測試案例。這三筆同為颱風季事件，
但存續時間從 2 日到 64 日、潰決原因從溢流沖刷到機具開挖，
剛好檢驗模板法能否正確區分。

執行：
    python3 test_attribution.py
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verbalize as V
from rules import (LakeRecord, Observations, attribute, infer_river,
                   normalize_breach_cause, typhoon_position, parse_duration_days)
from compose import Composer
from forecast import (BasinParams, LakeState, RainScenario, forecast,
                      remaining_capacity, volume_at, elevation_at)


COMPOSER = Composer(os.path.join(HERE, "templates.yaml"))


# ══════════════════════════════════════════
# 清冊 2025 年三筆（欄位取自原始 CSV）
# ══════════════════════════════════════════

CAOLING = LakeRecord(          # 項次 70
    seq=70, name="雲林清水溪(草嶺)", year=2025,
    county="雲林縣", town="古坑鄉", village="草嶺村",
    landmark="", cause="颱風", event="丹娜絲颱風",
    formed="2025/7/8", duration="2", volume=1400.0,
    breach_date="2025/07/10", breach_cause="溢流沖刷",
    status="消失", setting="河川",
    dam_xy=(216173, 2608410), slide_xy=(216812, 2609049),
)

MATAIAN = LakeRecord(          # 項次 71
    seq=71, name="花蓮馬太鞍溪", year=2025,
    county="花蓮縣", town="萬榮鄉", village="明利村",
    landmark="林田山第118林班", cause="颱風", event="薇帕颱風",
    formed="2025/7/21", duration="64", volume=9100.0,
    breach_date="2025/09/23", breach_cause="溢流沖刷",
    status="監測中", setting="林班地",
    dam_xy=(280340, 2621899), slide_xy=(280002, 2624183),
)

YANZIKOU = LakeRecord(         # 項次 72
    seq=72, name="花蓮立霧溪(燕子口)", year=2025,
    county="花蓮縣", town="秀林鄉", village="富世村",
    landmark="燕子口、立霧溪第20林班", cause="", event="",
    formed="2025/10/17", duration="9", volume=190.0,
    breach_date="2025/10/26", breach_cause="機具開挖",
    status="消失", setting="林班地",
    dam_xy=(306811, 2674313), slide_xy=(306759, 2674314),
)

JIUFEN = LakeRecord(           # 項次 3，九二一地震型、持續至今
    seq=3, name="九份二山(韭菜湖溪)", year=1999,
    county="南投縣", town="國姓鄉", village="南港村",
    landmark="南港溪支流木屐蘭溪上游", cause="地震", event="九二一地震",
    formed="1999/9/21", duration="持續至今", volume=68.0,
    breach_date="", breach_cause="",
    status="存在(已穩定)", setting="山坡地",
)


# ══════════════════════════════════════════
# verbalize
# ══════════════════════════════════════════

class TestVerbalize(unittest.TestCase):

    def test_duration_short_vs_long(self):
        """2 日與 64 日必須有明顯不同的表述，且長者附換算。"""
        self.assertEqual(V.duration(2), "2 日")
        self.assertIn("個月", V.duration(64))

    def test_duration_textual_forms(self):
        self.assertEqual(V.duration(None, "持續至今"), "持續至今")
        self.assertEqual(V.duration(None, "<24HR"), "不足 24 小時")
        self.assertEqual(V.duration(None, "1.5HR"), "約 1.5 小時")

    def test_duration_none_is_none(self):
        """未記載必須回 None，讓上層決定略過整句，而非輸出 '0 日'。"""
        self.assertIsNone(V.duration(None, ""))

    def test_rain_grade_official_thresholds(self):
        self.assertEqual(V.rain_grade(500), "extremely_torrential")
        self.assertEqual(V.rain_grade(350), "torrential")
        self.assertEqual(V.rain_grade(200), "extremely_heavy")
        self.assertEqual(V.rain_grade(80), "heavy")
        self.assertIsNone(V.rain_grade(79))

    def test_rain_grade_only_for_24h_window(self):
        """6 小時雨量不可套用 24 小時級距。"""
        self.assertIsNone(V.rain_grade(460, window_hours=6))

    def test_volume_precision_by_magnitude(self):
        self.assertEqual(V.volume_amount(9100), "9,100")
        self.assertEqual(V.volume_amount(108.97), "108.97")
        self.assertEqual(V.volume_amount(0.27), "0.27")
        self.assertIsNone(V.volume_amount(0))

    def test_volume_scale_boundaries(self):
        self.assertEqual(V.volume_scale(9100), "huge")
        self.assertEqual(V.volume_scale(1400), "large")
        self.assertEqual(V.volume_scale(190), "medium")
        self.assertEqual(V.volume_scale(68), "small")
        self.assertEqual(V.volume_scale(4), "tiny")

    def test_elevation_gap_half_up(self):
        """安全相關數字須採可預測的四捨五入，不用銀行家捨入。"""
        self.assertEqual(V.elevation_gap(0.35), "0.4")
        self.assertEqual(V.elevation_gap(6.44), "6.4")

    def test_absence_distinguishes_meanings(self):
        """『無此現象』與『未記載』不可混用。"""
        self.assertNotEqual(V.absence("not_recorded"), V.absence("none_occurred"))

    def test_staleness_tracks_revisit_cycle(self):
        self.assertEqual(V.staleness_key(timedelta(hours=6)), "fresh")
        self.assertEqual(V.staleness_key(timedelta(days=3)), "aging")
        self.assertEqual(V.staleness_key(timedelta(days=9)), "stale")


# ══════════════════════════════════════════
# rules
# ══════════════════════════════════════════

class TestRules(unittest.TestCase):

    def test_infer_river_strips_county_prefix(self):
        self.assertEqual(infer_river("花蓮馬太鞍溪"), "馬太鞍溪")
        self.assertEqual(infer_river("雲林清水溪(草嶺)"), "清水溪")
        self.assertEqual(infer_river("荖濃溪支流寶來溪上游堰塞湖"), "荖濃溪")

    def test_infer_river_returns_none_when_unnamed(self):
        """推不出河川名時整句略過，不可硬湊。"""
        self.assertIsNone(infer_river("九份二山"))
        self.assertIsNone(infer_river("合流坪"))

    def test_typhoon_position_thresholds(self):
        self.assertEqual(typhoon_position(60, None), "direct")
        self.assertEqual(typhoon_position(180, None), "outer")
        self.assertEqual(typhoon_position(450, True), "southwest_flow")
        self.assertEqual(typhoon_position(450, False), "distant")

    def test_breach_cause_normalization(self):
        self.assertEqual(normalize_breach_cause("溢流沖刷"), "overflow")
        self.assertEqual(normalize_breach_cause("機具開挖"), "excavation")
        self.assertEqual(normalize_breach_cause("豪雨沖刷"), "rainfall")
        self.assertIsNone(normalize_breach_cause(""))

    def test_duration_parse_rejects_text(self):
        self.assertEqual(parse_duration_days("64"), 64.0)
        self.assertIsNone(parse_duration_days("持續至今"))

    def test_no_observations_still_works(self):
        """沒有氣象觀測時仍可產出敘述，只是較短，且不得有臆測。"""
        attr = attribute(MATAIAN)
        self.assertIn("trigger.typhoon.text", attr.rules_fired)
        self.assertNotIn("distance", attr.slots)

    def test_rules_fired_is_auditable(self):
        """每筆輸出都要能追溯命中的規則。"""
        attr = attribute(CAOLING)
        self.assertTrue(all("." in r for r in attr.rules_fired))
        self.assertGreater(len(attr.rules_fired), 2)


# ══════════════════════════════════════════
# compose — 三筆真實紀錄的敘述
# ══════════════════════════════════════════

class TestNarrative(unittest.TestCase):

    def render(self, rec, obs=None):
        return COMPOSER.render(attribute(rec, obs))

    def test_no_unresolved_slots(self):
        """任何一筆都不得出現填不滿的槽位。"""
        for rec in (CAOLING, MATAIAN, YANZIKOU, JIUFEN):
            with self.subTest(rec.name):
                self.assertEqual(self.render(rec).unresolved, [])

    def test_never_emits_none_or_braces(self):
        """輸出中不得出現 None 或未填的大括號。"""
        for rec in (CAOLING, MATAIAN, YANZIKOU, JIUFEN):
            text = self.render(rec).text
            with self.subTest(rec.name):
                self.assertNotIn("None", text)
                self.assertNotIn("{", text)

    def test_caoling_short_lived(self):
        """草嶺：2 日潰決、溢流沖刷、大型。"""
        text = self.render(CAOLING).text
        self.assertIn("丹娜絲颱風", text)
        self.assertIn("2 日", text)
        self.assertIn("溢流沖刷", text)
        self.assertIn("大型", text)

    def test_mataian_long_lived_and_monitoring(self):
        """馬太鞍：64 日、極大型、現況監測中——不可寫成已潰決。"""
        text = self.render(MATAIAN).text
        self.assertIn("薇帕颱風", text)
        self.assertIn("極大型", text)
        self.assertIn("監測中", text)
        self.assertNotIn("潰決", text)

    def test_same_cause_different_fate(self):
        """
        兩者同為颱風型、同為溢流潰決，但存續 2 日 vs 64 日。
        敘述必須能區分，否則模板過於粗糙。
        """
        a = self.render(CAOLING).text
        b = self.render(MATAIAN).text
        self.assertNotEqual(a, b)

    def test_yanzikou_human_intervention(self):
        """燕子口是機具開挖，不是自然溢流——絕不可誤述。"""
        text = self.render(YANZIKOU).text
        self.assertIn("人為機具開挖", text)
        self.assertNotIn("溢流沖刷", text)

    def test_yanzikou_missing_cause_is_explicit(self):
        """誘因欄位空白時應明講未記載，不可自行推論成颱風。"""
        text = self.render(YANZIKOU).text
        self.assertIn("未記載", text)
        self.assertNotIn("颱風", text)

    def test_jiufen_quake_and_still_present(self):
        """九份二山：地震型、持續至今、已穩定。"""
        text = self.render(JIUFEN).text
        self.assertIn("九二一地震", text)
        self.assertIn("持續至今", text)
        self.assertIn("已穩定", text)

    def test_rain_clause_omitted_without_data(self):
        """沒有雨量資料時不得憑空生出雨量數字。"""
        text = self.render(MATAIAN).text
        self.assertNotIn("mm", text)

    def test_rain_clause_present_with_data(self):
        obs = Observations(typhoon_name="薇帕颱風", typhoon_distance_km=180.4,
                           rain_24h_mm=460.0, rain_percentile=99.4)
        text = self.render(MATAIAN, obs).text
        self.assertIn("460 mm", text)
        self.assertIn("大豪雨", text)
        self.assertIn("前 1%", text)

    def test_percentile_clause_omitted_when_ordinary(self):
        """百分位落在一般區間時，比較子句整句不輸出。"""
        obs = Observations(typhoon_name="薇帕颱風", rain_24h_mm=460.0,
                           rain_percentile=70.0)
        text = self.render(MATAIAN, obs).text
        self.assertIn("460 mm", text)
        self.assertNotIn("%", text)

    def test_deterministic(self):
        """同樣輸入必得同樣輸出——這是可回歸測試的前提。"""
        obs = Observations(typhoon_distance_km=180.4, rain_24h_mm=460.0)
        first = self.render(MATAIAN, obs).text
        for _ in range(5):
            self.assertEqual(self.render(MATAIAN, obs).text, first)


# ══════════════════════════════════════════
# forecast
# ══════════════════════════════════════════

CURVE = [(640, 0), (650, 180), (660, 900), (670, 3200), (676, 6800), (682, 9100)]


class TestForecast(unittest.TestCase):

    def setUp(self):
        self.state = LakeState(water_el=675.6, crest_el=682.0, floor_el=640.0,
                               hypsometric=CURVE,
                               observed_at=datetime(2026, 7, 23, 6, 12))
        self.params = BasinParams(catchment_km2=42.6,
                                  runoff_coefficient=0.72, seepage_cms=8.0)

    def test_curve_interpolation_and_inverse(self):
        v = volume_at(CURVE, 675.6)
        self.assertAlmostEqual(elevation_at(CURVE, v), 675.6, places=3)

    def test_curve_clamps_outside_range(self):
        self.assertEqual(volume_at(CURVE, 600), 0.0)
        self.assertEqual(volume_at(CURVE, 700), 9100.0)

    def test_remaining_capacity_positive(self):
        self.assertGreater(remaining_capacity(self.state), 0)

    def test_gap_matches_elevations(self):
        self.assertAlmostEqual(self.state.gap_m, 6.4, places=6)

    def test_returns_interval_not_point(self):
        """必須輸出區間。單一時間點在災防上是危險的假精確。"""
        heavy = [RainScenario("低", 15.0), RainScenario("中", 25.0),
                 RainScenario("高", 40.0)]
        fc = forecast(self.state, self.params, heavy)
        self.assertIsNotNone(fc.earliest)
        self.assertIsNotNone(fc.latest)
        self.assertLessEqual(fc.earliest, fc.latest)

    def test_heavier_rain_overflows_sooner(self):
        light = forecast(self.state, self.params, [RainScenario("x", 15.0)])
        heavy = forecast(self.state, self.params, [RainScenario("x", 40.0)])
        self.assertLess(heavy.scenarios[0].hours_to_overflow,
                        light.scenarios[0].hours_to_overflow)

    def test_no_extrapolation_beyond_horizon(self):
        """超出情境預報時距就回報無溢流，不外推。"""
        fc = forecast(self.state, self.params,
                      [RainScenario("微雨", 1.0, hours=72)])
        self.assertIsNone(fc.scenarios[0].overflow_at)

    def test_seepage_can_prevent_overflow(self):
        dry = BasinParams(catchment_km2=42.6, runoff_coefficient=0.72,
                          seepage_cms=200.0)
        fc = forecast(self.state, self.params_or(dry),
                      [RainScenario("小雨", 1.0)])
        self.assertFalse(fc.any_overflow)

    def params_or(self, p):
        return p

    def test_assumptions_are_disclosed(self):
        """逕流係數等假設必須隨輸出揭露。"""
        fc = forecast(self.state, self.params)
        self.assertIn("逕流係數", fc.assumptions)

    def test_staleness_flagged(self):
        fc = forecast(self.state, self.params,
                      now=self.state.observed_at + timedelta(days=9))
        self.assertEqual(fc.staleness, "stale")


if __name__ == "__main__":
    unittest.main(verbosity=2)
