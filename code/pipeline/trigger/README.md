# trigger — 觸發層（架構文件 §04）

已實作（v1，簡化版）：

- `thresholds.py` — 規則核心：規模 ≥5.5＋山區測站震度 ≥5弱、時雨量
  ≥80mm 或 24h≥350mm、地震後 30 天內豪雨門檻下修 20%。純函式、不碰網路。
- `earthquake.py` — CWA 顯著有感地震報告（E-A0015-001）擷取，比照
  `ingest/cwa.py` 的離線降級風格。**尚未拿真實金鑰測過實際回應**，
  接上金鑰後務必核對欄位路徑。
- `catchment.py` — 集水區圈定：目前是地理半徑近似（`circle_aoi`），
  疊合本專案既有清冊標出範圍內已知湖泊（`lakes_in_aoi`，這是唯一真實
  地理資料的依據）；有 DEM 時可選用性套用坡度 >30° 篩選
  （`slope_filter_aoi`，重用 `preprocess/mask.py`）。
- `dispatch.py` — CDSE 影像調度骨架：OAuth2 token 交換、STAC 查詢事件前
  基準景與事件後/未來景；查不到時退回重訪週期估計值
  （`estimate_next_pass`，明確標註 `estimated: True`）。**尚未拿真實
  CDSE 帳號測過**。
- `tasking.py` — 把上述結果組成 `MonitoringTask`（純資料結構）；
  `build_task_from_quake`／`build_task_from_rain`／`build_tasks` 現在支援
  `enable_dispatch=True`，任務建立後立刻呼叫 `dispatch_for_task()` 查一次
  CDSE（架構文件 §04「任務建立後自動向 CDSE 查詢」），結果存進
  `task.dispatch`；預設關閉，避免單元測試或離線流程意外連網。
- `service.py` — 輪詢入口：`--once`／`--poll`／`--offline`／`--demo`／
  `--no-dispatch`；`check_once()` 預設在非 offline 時開啟
  `enable_dispatch`（CDSE token 每次只換一次、多個任務共用，不逐一重換）；
  跨輪次用 `data/derived/trigger_state.json` 記住最近一次達門檻地震的
  時間，供複合加權判斷；輸出 `dashboard/data/trigger_tasks.js`。
  `--poll` 有裝 `apscheduler`（見 requirements.txt）就用 `BlockingScheduler`
  常駐排程（架構文件 §11 技術棧表），沒裝就自動退回陽春 `while` 迴圈，
  兩條路徑都呼叫同一個 `check_once()`。
- **前端串接** — `dashboard/app.js` 讀取 `window.TRIGGER_TASKS`，在地圖上方
  的「§04 觸發層」面板列出監測任務（類型、優先度、附近已知湖泊、影像調度
  狀態），點擊任務會在 3D 地圖上高亮其 AOI；`dashboard/map3d.js` 新增
  `setTriggerAreas()`／`setActiveTriggerTask()`，用獨立的琥珀色圈
  （`COL.trigger`）跟 CAP 範圍圈（jade 色）區隔，因為兩者可能同時存在、
  且觸發任務的中心不一定落在任何已知湖泊標記上。

尚未整合：

- **真實集水區向量圖資**（水利署圖資）——`circle_aoi` 是暫代值，
  介面已預留給真實 polygon（見 `catchment.py` 檔頭 TODO）。
- **未接入 `build_all.py`**——跟 `preprocess`/`detect`/`assess` 不同，
  觸發層是常駐服務、不是一次性重建流程的一部分，所以不會加進
  `build_all.py`；但 `service.py --once --offline` 可以當成 smoke test
  跑，確認流程沒壞。`dashboard/data/trigger_tasks.js` 需要手動跑一次
  `service.py --once`（或 `--demo`）才會有內容，這是刻意的（觸發層的
  資料本來就該由常駐服務產生，不是重建腳本）。
