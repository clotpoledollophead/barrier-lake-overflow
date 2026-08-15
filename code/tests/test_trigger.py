#!/usr/bin/env python3
"""
test_trigger.py — pipeline.trigger 單元測試

只測不連網的部分：門檻規則、複合加權、集水區圈定的幾何與湖泊比對、
任務組裝、以及 service.check_once() 在 offline/demo/依賴注入三種模式下
是否正常運作並寫出合法的 trigger_tasks.js。真正呼叫 CWA 地震 API
（pipeline.trigger.earthquake.fetch_significant_earthquakes）與
CDSE（pipeline.trigger.dispatch）不在本檔測試範圍內——那需要真的
網路與金鑰，跟 test_risk.py 對 pipeline.ingest.cwa 的處理方式一致。

執行（於 code/ 目錄下）：
    pytest tests/test_trigger.py -v
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta

from pipeline.trigger import catchment as C
from pipeline.trigger import service as S
from pipeline.trigger import tasking as T
from pipeline.trigger import thresholds as TH

FIXTURE_LAKES_JS = (
    "/* 測試用 fixture */\n"
    "window.BARRIER_LAKES = [\n"
    ' {"name": "馬太鞍溪堰塞湖", "lat": 23.68, "lon": 121.43,'
    ' "statusKey": "watch"},\n'
    ' {"name": "遠方湖", "lat": 25.0, "lon": 121.5,'
    ' "statusKey": "stable"},\n'
    ' {"name": "已消失湖", "lat": 23.70, "lon": 121.44,'
    ' "statusKey": "gone"}\n'
    "];\n"
)


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ── thresholds.py ────────────────────────────────────────

class TestIntensityScale(unittest.TestCase):
    def test_ordering(self):
        self.assertGreater(TH.intensity_rank("5弱"), TH.intensity_rank("4"))
        self.assertGreater(TH.intensity_rank("6強"), TH.intensity_rank("5弱"))
        self.assertGreater(TH.intensity_rank("5強"), TH.intensity_rank("5弱"))

    def test_numeric_fallback(self):
        # 舊制只給數字時，視同該級的「弱」
        self.assertEqual(TH.intensity_rank("5"), TH.intensity_rank("5弱"))

    def test_unknown_label_is_lowest(self):
        self.assertEqual(TH.intensity_rank("不明"), -1)

    def test_at_least(self):
        self.assertTrue(TH.intensity_at_least("5弱", "5弱"))
        self.assertTrue(TH.intensity_at_least("6弱", "5弱"))
        self.assertFalse(TH.intensity_at_least("4", "5弱"))


class TestQuakeTrigger(unittest.TestCase):
    def test_magnitude_and_intensity_both_required(self):
        st = [TH.EarthquakeStation("測站A", 23.6, 121.4, "5弱")]
        weak_quake = TH.EarthquakeObservation(datetime(2025, 9, 1), 5.0, 23.7, 121.4, st)
        strong_quake = TH.EarthquakeObservation(datetime(2025, 9, 1), 6.0, 23.7, 121.4, st)
        self.assertFalse(TH.quake_exceeds_threshold(weak_quake).triggered)
        self.assertTrue(TH.quake_exceeds_threshold(strong_quake).triggered)

    def test_no_qualifying_station_means_no_trigger(self):
        st = [TH.EarthquakeStation("測站A", 23.6, 121.4, "3")]
        q = TH.EarthquakeObservation(datetime(2025, 9, 1), 6.5, 23.7, 121.4, st)
        self.assertFalse(TH.quake_exceeds_threshold(q).triggered)

    def test_non_mountain_station_excluded_by_elevation(self):
        # 有海拔資料且海拔不足 100m 時，即使在山地 bbox 內也不算山區測站
        st = [TH.EarthquakeStation("平地測站", 23.6, 121.4, "6弱", elevation_m=20.0)]
        q = TH.EarthquakeObservation(datetime(2025, 9, 1), 7.0, 23.7, 121.4, st)
        self.assertFalse(TH.quake_exceeds_threshold(q).triggered)


class TestCompoundWeighting(unittest.TestCase):
    def test_within_window_applies_discount(self):
        d = TH.compound_discount(datetime(2025, 9, 1), datetime(2025, 9, 20))
        self.assertAlmostEqual(d, TH.COMPOUND_DISCOUNT)

    def test_outside_window_no_discount(self):
        d = TH.compound_discount(datetime(2025, 9, 1), datetime(2025, 11, 1))
        self.assertEqual(d, 0.0)

    def test_no_prior_quake_no_discount(self):
        self.assertEqual(TH.compound_discount(None, datetime(2025, 9, 1)), 0.0)

    def test_rain_threshold_lowered_by_discount(self):
        rain_time = datetime(2025, 9, 20)
        quake_time = datetime(2025, 9, 1)
        # 79mm：平時不過門檻，但複合加權後門檻降到 80*0.8=64mm，應觸發
        station = TH.RainfallStation("測站B", 23.6, 121.4, rain_1h_mm=70.0)
        normal = TH.rain_exceeds_threshold(station, rain_time, last_significant_quake_time=None)
        compound = TH.rain_exceeds_threshold(station, rain_time, last_significant_quake_time=quake_time)
        self.assertFalse(normal.triggered)
        self.assertTrue(compound.triggered)
        self.assertEqual(compound.trigger_type, "compound")


class TestRainTrigger(unittest.TestCase):
    def test_hourly_or_daily_either_triggers(self):
        hourly = TH.RainfallStation("A", 23.6, 121.4, rain_1h_mm=90.0, rain_24h_mm=10.0)
        daily = TH.RainfallStation("B", 23.6, 121.4, rain_1h_mm=5.0, rain_24h_mm=400.0)
        neither = TH.RainfallStation("C", 23.6, 121.4, rain_1h_mm=5.0, rain_24h_mm=10.0)
        t = datetime(2025, 9, 1)
        self.assertTrue(TH.rain_exceeds_threshold(hourly, t).triggered)
        self.assertTrue(TH.rain_exceeds_threshold(daily, t).triggered)
        self.assertFalse(TH.rain_exceeds_threshold(neither, t).triggered)


# ── catchment.py ─────────────────────────────────────────

class TestCatchment(unittest.TestCase):
    def test_haversine_zero_distance(self):
        self.assertAlmostEqual(C.haversine_km(23.67, 121.42, 23.67, 121.42), 0.0)

    def test_haversine_known_rough_distance(self):
        # 花蓮 -> 台北，粗略應在 130~180 km 之間（只驗證數量級，不要求精確）
        d = C.haversine_km(23.99, 121.60, 25.03, 121.56)
        self.assertGreater(d, 100)
        self.assertLess(d, 200)

    def test_polygon_ring_point_count(self):
        aoi = C.circle_aoi(23.67, 121.42, 30.0)
        ring = C.polygon_ring(aoi, n_points=8)
        self.assertEqual(len(ring), 8)

    def test_lakes_in_aoi_filters_by_radius(self):
        aoi = C.circle_aoi(23.68, 121.43, 30.0)
        lakes = json.loads(re.search(
            r"window\.BARRIER_LAKES\s*=\s*(\[.*?\]);", FIXTURE_LAKES_JS, re.S
        ).group(1))
        hits = C.lakes_in_aoi(aoi, lakes)
        names = {h["name"] for h in hits}
        self.assertIn("馬太鞍溪堰塞湖", names)
        self.assertIn("已消失湖", names)      # 30km 內，即使已消失也該回傳（過濾交給呼叫端）
        self.assertNotIn("遠方湖", names)      # 距離 > 30km

    def test_lakes_in_aoi_sorted_by_distance(self):
        aoi = C.circle_aoi(23.68, 121.43, 30.0)
        lakes = json.loads(re.search(
            r"window\.BARRIER_LAKES\s*=\s*(\[.*?\]);", FIXTURE_LAKES_JS, re.S
        ).group(1))
        hits = C.lakes_in_aoi(aoi, lakes)
        distances = [h["distance_km"] for h in hits]
        self.assertEqual(distances, sorted(distances))


# ── tasking.py ───────────────────────────────────────────

class TestTasking(unittest.TestCase):
    def setUp(self):
        self.lakes = json.loads(re.search(
            r"window\.BARRIER_LAKES\s*=\s*(\[.*?\]);", FIXTURE_LAKES_JS, re.S
        ).group(1))

    def test_quake_below_threshold_returns_none(self):
        weak = TH.EarthquakeObservation(
            datetime(2025, 9, 1), 5.0, 23.7, 121.4,
            [TH.EarthquakeStation("A", 23.6, 121.4, "5弱")])
        self.assertIsNone(T.build_task_from_quake(weak, self.lakes))

    def test_quake_task_includes_nearby_lakes_and_high_priority(self):
        strong = TH.EarthquakeObservation(
            datetime(2025, 9, 1), 6.5, 23.68, 121.43,
            [TH.EarthquakeStation("A", 23.6, 121.4, "6弱")])
        task = T.build_task_from_quake(strong, self.lakes)
        self.assertIsNotNone(task)
        self.assertEqual(task.trigger_type, "quake")
        self.assertEqual(task.priority, "high")
        self.assertTrue(any(lk["name"] == "馬太鞍溪堰塞湖" for lk in task.nearby_known_lakes))

    def test_rain_task_marks_compound_when_recent_quake(self):
        station = TH.RainfallStation("光復", 23.67, 121.42, rain_1h_mm=70.0)
        rain_time = datetime(2025, 9, 20)
        quake_time = datetime(2025, 9, 1)
        task = T.build_task_from_rain(station, rain_time, self.lakes,
                                       last_significant_quake_time=quake_time)
        self.assertIsNotNone(task)
        self.assertEqual(task.trigger_type, "compound")
        self.assertEqual(task.priority, "high")

    def test_build_tasks_combines_quake_and_rain(self):
        quakes = [TH.EarthquakeObservation(
            datetime(2025, 9, 1), 6.5, 23.68, 121.43,
            [TH.EarthquakeStation("A", 23.6, 121.4, "6弱")])]
        rain_stations = [TH.RainfallStation("光復", 23.67, 121.42, rain_1h_mm=90.0)]
        tasks = T.build_tasks(quakes, rain_stations, datetime(2025, 9, 1), self.lakes)
        self.assertEqual(len(tasks), 2)
        self.assertEqual({t.trigger_type for t in tasks}, {"quake", "rain"})

    def test_task_to_dict_is_json_serializable(self):
        quakes = [TH.EarthquakeObservation(
            datetime(2025, 9, 1), 6.5, 23.68, 121.43,
            [TH.EarthquakeStation("A", 23.6, 121.4, "6弱")])]
        task = T.build_tasks(quakes, [], datetime(2025, 9, 1), self.lakes)[0]
        json.dumps(task.to_dict(), ensure_ascii=False)  # 不該丟例外

    def test_dispatch_disabled_by_default_no_network(self):
        # 預設 enable_dispatch=False，task.dispatch 應該保持 None，
        # 且完全不該嘗試連線（no CDSE_CLIENT_ID/SECRET 的測試環境下
        # 若真的呼叫 dispatch，get_access_token 會回 None，但這裡驗證
        # 的是「根本沒被呼叫」這件事本身──用 dispatch 為 None 佐證）。
        quakes = [TH.EarthquakeObservation(
            datetime(2025, 9, 1), 6.5, 23.68, 121.43,
            [TH.EarthquakeStation("A", 23.6, 121.4, "6弱")])]
        task = T.build_task_from_quake(quakes[0], self.lakes)
        self.assertIsNone(task.dispatch)

    def test_dispatch_enabled_attaches_result_without_credentials(self):
        # 沒有 CDSE 憑證時，dispatch_for_task 應該優雅降級（回傳 dict，
        # nextScene 帶 estimated=True），不丟例外、不需要真的連網。
        quakes = [TH.EarthquakeObservation(
            datetime(2025, 9, 1), 6.5, 23.68, 121.43,
            [TH.EarthquakeStation("A", 23.6, 121.4, "6弱")])]
        task = T.build_task_from_quake(quakes[0], self.lakes, enable_dispatch=True, cdse_token=None)
        self.assertIsNotNone(task.dispatch)
        self.assertIsNone(task.dispatch["latestScene"])
        self.assertTrue(task.dispatch["nextScene"]["estimated"])
        self.assertFalse(task.dispatch["hasCdseCredentials"])
        json.dumps(task.to_dict(), ensure_ascii=False)  # next_pass_eta 必須已轉成字串，不能丟例外

    def test_build_tasks_enable_dispatch_applies_to_all_tasks(self):
        quakes = [TH.EarthquakeObservation(
            datetime(2025, 9, 1), 6.5, 23.68, 121.43,
            [TH.EarthquakeStation("A", 23.6, 121.4, "6弱")])]
        rain_stations = [TH.RainfallStation("光復", 23.67, 121.42, rain_1h_mm=90.0)]
        tasks = T.build_tasks(quakes, rain_stations, datetime(2025, 9, 1), self.lakes,
                               enable_dispatch=True)
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(t.dispatch is not None for t in tasks))


# ── service.py ───────────────────────────────────────────

class TestServiceOffline(unittest.TestCase):
    def test_offline_mode_writes_zero_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            state_path = os.path.join(d, "state.json")
            out_path = os.path.join(d, "trigger_tasks.js")

            tasks = S.check_once(lakes_path=lakes_path, state_path=state_path,
                                  out_path=out_path, offline=True)

            self.assertEqual(tasks, [])
            text = open(out_path, encoding="utf-8").read()
            self.assertIn("window.TRIGGER_TASKS = []", text)

    def test_demo_mode_produces_at_least_one_task(self):
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            state_path = os.path.join(d, "state.json")
            out_path = os.path.join(d, "trigger_tasks.js")

            tasks = S.check_once(lakes_path=lakes_path, state_path=state_path,
                                  out_path=out_path, demo=True)

            self.assertGreaterEqual(len(tasks), 1)
            self.assertTrue(any(t.trigger_type == "quake" for t in tasks))
            # demo 情境的地震規模夠大，狀態檔應該被寫入供之後複合加權判斷用
            state = S.load_state(state_path)
            self.assertIsNotNone(state["last_significant_quake_time"])

    def test_injected_fetchers_avoid_real_network(self):
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            state_path = os.path.join(d, "state.json")
            out_path = os.path.join(d, "trigger_tasks.js")

            fake_quake = TH.EarthquakeObservation(
                datetime.utcnow(), 6.8, 23.68, 121.43,
                [TH.EarthquakeStation("假測站", 23.6, 121.4, "6強")])

            def fake_quake_fetcher(_api_key):
                return [fake_quake]

            def fake_rain_fetcher(_api_key):
                return [{"lat": 23.67, "lon": 121.42, "r24h": 400.0}]

            tasks = S.check_once(
                lakes_path=lakes_path, state_path=state_path, out_path=out_path,
                quake_fetcher=fake_quake_fetcher, rain_fetcher=fake_rain_fetcher,
            )

            self.assertEqual(len(tasks), 2)
            # 假地震剛好發生在「現在」，雨量也是「現在」抓到的，兩者相距 0 天，
            # 落在複合加權 30 天窗口內，所以雨量任務會被判成 "compound" 而非
            # 單純的 "rain"——這正是 §04「地震後 30 天內遇豪雨 → 門檻下修」
            # 端到端生效的證據，不是測試寫錯。
            self.assertEqual({t.trigger_type for t in tasks}, {"quake", "compound"})

    def test_dispatch_images_true_attaches_dispatch_to_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            state_path = os.path.join(d, "state.json")
            out_path = os.path.join(d, "trigger_tasks.js")

            tasks = S.check_once(lakes_path=lakes_path, state_path=state_path,
                                  out_path=out_path, demo=True, dispatch_images=True)

            self.assertTrue(all(t.dispatch is not None for t in tasks))
            text = open(out_path, encoding="utf-8").read()
            self.assertIn('"dispatch"', text)
            self.assertIn('"nextScene"', text)  # 確認 dispatch 有寫進 trigger_tasks.js

    def test_dispatch_images_false_leaves_dispatch_none(self):
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            state_path = os.path.join(d, "state.json")
            out_path = os.path.join(d, "trigger_tasks.js")

            tasks = S.check_once(lakes_path=lakes_path, state_path=state_path,
                                  out_path=out_path, demo=True, dispatch_images=False)

            self.assertTrue(all(t.dispatch is None for t in tasks))

    def test_offline_skips_dispatch_even_if_dispatch_images_true(self):
        # --offline 優先於 dispatch_images：不連網原則不該被這個旗標繞過。
        with tempfile.TemporaryDirectory() as d:
            lakes_path = _write(d, "lakes.js", FIXTURE_LAKES_JS)
            state_path = os.path.join(d, "state.json")
            out_path = os.path.join(d, "trigger_tasks.js")

            tasks = S.check_once(lakes_path=lakes_path, state_path=state_path,
                                  out_path=out_path, offline=True, dispatch_images=True)

            self.assertEqual(tasks, [])  # offline 本來就 0 個任務，這裡主要驗證不會噴例外


class TestPollLoop(unittest.TestCase):
    def test_poll_loop_uses_apscheduler_when_available(self):
        # 不用真的常駐等待，只驗證「有裝 apscheduler 時會走 BlockingScheduler
        # 這條路徑」──用 monkeypatch 讓 scheduler.start() 立刻丟
        # KeyboardInterrupt，模擬「使用者按 Ctrl+C」，藉此驗證程式碼路徑
        # 有沒有正確建立 job 並嘗試啟動，而不必真的跑一次 interval。
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
        except ImportError:
            self.skipTest("apscheduler 未安裝，這個環境會走陽春迴圈備援路徑")

        calls = []
        original_start = BlockingScheduler.start

        def fake_start(self):
            calls.append(True)
            raise KeyboardInterrupt

        BlockingScheduler.start = fake_start
        try:
            S.poll_loop(interval_sec=1, offline=True)
        finally:
            BlockingScheduler.start = original_start

        self.assertEqual(calls, [True])

    def test_poll_loop_falls_back_without_apscheduler(self):
        # 模擬「沒裝 apscheduler」：把 sys.modules 裡的入口暫時擋掉，
        # 確認會印出提示並走陽春迴圈（用 time.sleep 丟 KeyboardInterrupt
        # 讓迴圈只跑一次就結束，不用真的等一個 interval）。
        import sys as _sys
        import pipeline.trigger.service as svc_mod

        blocked = {k: v for k, v in _sys.modules.items()
                   if k.startswith("apscheduler")}
        for k in blocked:
            del _sys.modules[k]
        _sys.modules["apscheduler"] = None  # import 會直接 ImportError

        calls = {"check_once": 0}
        original_check_once = svc_mod.check_once
        original_sleep = svc_mod.time.sleep

        def fake_check_once(**kwargs):
            calls["check_once"] += 1

        def fake_sleep(_seconds):
            raise KeyboardInterrupt

        svc_mod.check_once = fake_check_once
        svc_mod.time.sleep = fake_sleep
        try:
            svc_mod.poll_loop(interval_sec=1, offline=True)
        finally:
            svc_mod.check_once = original_check_once
            svc_mod.time.sleep = original_sleep
            del _sys.modules["apscheduler"]
            _sys.modules.update(blocked)

        self.assertEqual(calls["check_once"], 1)


if __name__ == "__main__":
    unittest.main()
