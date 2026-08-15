# OSSInt 2026 · 堰塞湖快速評估系統

事件觸發式的堰塞湖監測管線：地震或豪雨達門檻即自動圈定高風險集水區、
調度衛星影像，偵測新生崩塌與河道新增水體，以 DEM 量化蓄水量與壩體規模，
並輸出可稽核的成因敘述與溢流預報。

**開源空間資訊於國家韌性應用黑客松競賽（OSSInt 2026）· 災害防救賽道**
提案投件截止 2026-09-10

---

## 目前狀態

| 模組 | 狀態 |
|---|---|
| 清冊資料轉換（TWD97 → WGS84） | ✅ 75 筆 |
| 全台分布儀表板 | ✅ |
| 成因歸因與敘述生成 | ✅ 39 項測試 |
| 溢流預報（水量平衡） | ✅ 核心完成，並已接進 `pipeline.build_all --demo-dem` 產生 `dashboard/data/forecast.js`；只在查得到公開可信集水面積來源時才有結果（目前僅花蓮馬太鞍溪，見「CAP 的風險依據」小節），待接 QPF |
| 風險模型（ERA5-Land + 邏輯迴歸，係數與雨量來源採用 package 版本） | ✅ 71/75 筆有評估，CWA 即時／offline 佔位皆可；CAP severity/urgency/certainty 唯一依據，見「CAP 的風險依據」小節 |
| CAP 示警輸出 | ✅ demo（`status=Test`），area 優先用 assess 算出的淹沒多邊形，沒資料才退回 circle |
| CWA API 介接 | ✅ 已接（見 `pipeline.ingest.cwa` / `risk`） |
| SAR 前處理與偵測（`preprocess` / `detect`） | 🟡 模組已實作（v1）並通過 31 項測試，**尚未接進** `build_all.py` 與儀表板——需要真實 Sentinel-1 影像，本專案沒有隨附，也不用合成影像頂替（見 `pipeline/assess/run.py` 檔頭說明為何跟 assess 的合成地形是不同等級的事） |
| DEM 量化與淹沒模擬（`assess`） | ✅ 核心已接進 `pipeline.build_all --demo-dem`：`hypsometry`＋`inundation` 串成端到端流程，輸出 `dashboard/data/inundation.js` 供 `cap.js` 取代 circle。真實 DEM 尚未隨附，預設用 `synthetic_dem.py` 產生的合成山谷+攔阻壩地形頂著（蓄水量已校準至清冊登載數字，其餘地形參數非真實測量值），輸出與前端皆標註 `method: "synthetic_demo_dem"`。`exposure.py`（人口/道路暴露）仍未接入，見 `assess/README.md` |
| 觸發層：門檻判斷與任務排程（`trigger`） | 🟡 v1（簡化版）：地震/雨量門檻＋複合加權、地理半徑近似集水區＋已知湖泊比對、CDSE 影像調度（任務建立後自動查詢，見 `tasking.dispatch_for_task`）、前端讀取 `trigger_tasks.js`（地圖疊「§04 觸發層」面板 + AOI 圈）、`--poll` 有裝 apscheduler 就用 `BlockingScheduler`都已實作並通過 33 項測試；**真實集水區向量圖資仍待做**，見 `code/pipeline/trigger/README.md` |

🟡 = 程式碼與單元測試已完成，但還沒串進一鍵重建流程（`pipeline.build_all`）或前端。
串接進度請見各子目錄 `README.md`（`code/pipeline/preprocess/README.md`、`detect/README.md`、
`assess/README.md`、`trigger/README.md`）。

---

## 怎麼執行

依你是誰、要做什麼，分成三種情境：

| 你是... | 該做什麼 | 要裝什麼 |
|---|---|---|
| 開發者，要改程式碼 / 加資料 | 看下面「開發環境」 | Python 3.11+ |
| 只是想看目前的儀表板結果 | 直接雙擊 `code/dashboard/index.html` | 不用裝任何東西 |
| 要把整包丟給不方便裝軟體的人，且資料會更新 | 看下面「打包給別人（雙擊即用）」 | 收件人端不用裝任何東西 |

### 開發環境

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd code && pip install -e .
```

`pip install -e .` 把 `pipeline` 裝成可編輯套件，之後任何位置都能
`from pipeline.attribution import ...`，不需要 `sys.path` 手動插入。

裝好之後，全部在 `code/` 目錄下執行：

```bash
# 測試（141 項：attribution 39、trigger 33、assess_pipeline 19、assess 13、
#        forecast_run 11、detect 11、risk 8、preprocess 7）
pytest

# 一鍵重建所有前端資料：清冊 → lakes.js、風險模型 → risk.js、成因敘述加註
python -m pipeline.build_all

# 加 --demo-dem：額外跑 assess + forecast 步驟（demo 用，非真實 DEM）
# 產生淹沒多邊形 → inundation.js，讓 cap.js 的 area 從 circle 換成真的多邊形
# 並在查得到集水面積來源時產生 forecast.js（溢流時間預報，見「CAP 的風險依據」）
python -m pipeline.build_all --demo-dem

# 加 --live：風險模型改抓 CWA 即時雨量，而不是用訓練平均值佔位
# （需先 export CWA_API_KEY="CWA-你的授權碼"，見 opendata.cwa.gov.tw；
#   沒設金鑰會自動退回 offline 佔位，不會讓指令失敗）
python -m pipeline.build_all --live

# 或分開單獨跑：

# 清冊 CSV → 前端資料（座標轉換）
python -m pipeline.ingest.inventory \
    ../data/raw/taiwan-barrier-lakes.csv dashboard/data/lakes.js

# 為全清冊產生成因敘述，寫回 lakes.js
python -m pipeline.attribution.annotate

# 風險模型 → 前端 risk.js（CAP 示警的資料來源）
# 模型公式／係數固定寫在 pipeline/ingest/risk.py（採用 package 版本）；
# 雨量特徵抓 CWA 即時觀測，沒有金鑰時退回訓練平均值佔位。
export CWA_API_KEY="CWA-你的授權碼"       # opendata.cwa.gov.tw 免費申請
python -m pipeline.ingest.risk
# 沒有金鑰、只想先驗證串接：
python -m pipeline.ingest.risk --offline

# 各模組的 doctest 與示範
python -m pipeline.attribution.verbalize
python -m pipeline.attribution.rules
python -m pipeline.attribution.forecast
python -m pipeline.attribution.compose

# 觸發層：檢查一次地震/雨量是否達門檻，建立監測任務
# （見 code/pipeline/trigger/README.md，v1 簡化版，尚未接進 build_all）
export CWA_API_KEY="CWA-你的授權碼"
python -m pipeline.trigger.service --once
# 沒有金鑰／先測流程：
python -m pipeline.trigger.service --once --offline
# 用內建合成情境（規模 7.2、震央擺在馬太鞍溪堰塞湖附近）跑一次展示：
# 會寫出 dashboard/data/trigger_tasks.js，重新整理 index.html 就能在
# 地圖上看到「§04 觸發層」面板與觸發範圍圈
python -m pipeline.trigger.service --once --demo
# 常駐輪詢（預設每 10 分鐘一次，Ctrl+C 結束；有裝 apscheduler 用
# BlockingScheduler，沒裝就退回陽春迴圈）：
python -m pipeline.trigger.service --poll
```

跑完後，`code/dashboard/data/*.js` 就是最新資料，直接雙擊
`code/dashboard/index.html` 就能看，沒有其他建置步驟。

### 打包給別人（雙擊即用）

`run_dashboard.bat`（根目錄）讓收件人不用裝 Python、不用裝任何東西，
雙擊就會重新產生資料並自動開啟儀表板。原理是把一份「embeddable」
Python（單純一個資料夾，不是安裝程式）跟專案放在一起，`.bat` 直接呼叫
那份 Python 執行 `pipeline.build_all`。

這個設定**只有你（打包的人）需要做一次**：完整步驟見
[`docs/portable-distribution.md`](docs/portable-distribution.md)。
做完之後，把整個專案資料夾（含設定好的 `python-embed/`）壓縮丟給誰，
對方解壓縮、雙擊 `run_dashboard.bat` 即可，Windows 專用。

如果收件人只需要看你這邊已經整理好的結果、不需要自己重新產生資料，
可以更省事：你自己先跑一次上面「開發環境」或 `.bat`，資料就寫進
`code/dashboard/data/*.js` 了，直接把整個資料夾（不含 `python-embed/`
也沒關係）打包過去，對方雙擊 `index.html` 就能看，檔案更小。

---

## 目錄結構

```
ossint-2026/
├── code/
│   ├── pyproject.toml
│   ├── pipeline/              Python 套件，依資料流階段劃分
│   │   ├── build_all.py       一鍵串接 ingest → risk → attribution → assess → forecast（--demo-dem 才跑後兩者）
│   │   ├── trigger/           觸發層：門檻判斷、任務排程          🟡 v1 簡化版，未接入 build_all/前端
│   │   ├── ingest/            資料取得（清冊、CWA）              ✅
│   │   ├── preprocess/        影像前處理                        🟡 已實作，未接入 build_all
│   │   ├── detect/            偵測                              🟡 已實作，未接入 build_all
│   │   ├── assess/            量化評估                          ✅ 核心已接入 build_all --demo-dem
│   │   └── attribution/       成因歸因、溢流預報（forecast_run.py 接 assess 曲線）✅
│   ├── tests/
│   └── dashboard/             前端，純靜態
├── data/
│   ├── raw/                   原始資料（小型 CSV 進版控）
│   └── derived/               中繼產物（不進版控）
├── docs/                      架構報告、資料圖鑑、模組說明、打包說明
├── people/                    各成員的 PROGRESS.md
├── run_dashboard.bat        給收件人雙擊用（見上方「怎麼執行」）
├── SCHEDULE.md
├── requirements.txt
└── .gitignore
```

子套件按**管線階段**劃分而非技術類別，目的是讓人看目錄就知道東西該往
哪放。`trigger`、`preprocess`、`detect`、`assess` 四個目錄下各有一份
README 說明預定內容與尚未串接的地方。

---

## 資料來源

清冊來自**農業部農村發展及水土保持署**堰塞湖清冊
（<https://tech.ardswc.gov.tw/Results/BarrierLakeInfo>），
共 75 筆、1979–2026 年。原始座標為 TWD97 TM2（EPSG:3826），
`pipeline/ingest/inventory.py` 轉為 WGS84。

完整的資料源清單、授權條件與註冊清單見 `docs/ossint-open-data-atlas.html`。

---

## 兩個設計決定

**不使用 LLM 產生敘述。** 災防系統的輸出必須可稽核——每一句話都要能追到
是哪條規則、哪個門檻產生的。`attribution` 採規則判定加模板填槽，
輸出附帶 `rules_fired` 清單。詳見 `docs/attribution.md`。

**預報一律輸出區間。** 「38 小時後溢流」這種假精確在災防上是危險的。
高／中／低三個雨量情境各算一次，回傳最早、中位、最晚，並揭露
逕流係數等假設。

---

## 已知限制

Sentinel-1 約 6 日重訪，兩次觀測之間水位是純靠雨量外推、沒有實測校正。
這是預報能力最脆弱的環節，每次輸出都會附上這項聲明。緩解方案是介接
現地水位計或以上游雨量站作代理指標加密更新。

DEM 為事件前地形，壩體形成後河床已改變、湖區持續淤積，蓄水量估算會隨
時間漂移，每次有新影像時需重新校正水位–容積曲線。

CAP 示警（`dashboard/cap.js`）目前是 demo：`status` 固定為 `Test`，
絕不可直接改成 `Actual` 對外發送。風險分級來自邏輯迴歸模型的批次快照，
正樣本僅 12 筆且未做留出驗證，`certainty` 因此最高只給到 `Possible`；
`area` 已經接上真的淹沒多邊形（`pipeline.assess.run`，`build_all --demo-dem`
才會產生），但目前專案沒有隨附真實 DEM，預設用 `synthetic_dem.py` 的
合成山谷+攔阻壩地形頂著——蓄水量已校準至清冊登載數字，谷寬/坡度/壩高
等其餘地形參數不是真實測量值，`method: "synthetic_demo_dem"` 與
disclaimer 會一路帶到 CAP parameters 與前端判定依據卡片，不會被當成
真的地形分析結果呈現；沒開 `--demo-dem` 且沒有真實 DEM 時則退回舊版
固定半徑 `circle`。這些都須在正式對外前逐一處理，不能只改 `status` 就上線。

風險模型本身不知道湖體現況是否還存在——`cap.js` 以清冊 `statusKey`
把關：「已消失」一律 `urgency=Past`／`responseType=AllClear`，
「存在(已穩定)」則把模型判的高風險下修一級，只有「監測中」才直接
採信模型輸出。這層把關若拿掉，24 筆模型判高風險裡有 23 筆其實是
已消失或已穩定的湖，會是很嚴重的「狼來了」假警報。

---

## CAP 的風險依據：兩個模型，兩個角色，不是二選一

本專案有兩套獨立的風險相關模型，容易被誤會成「該挑一個當正式版」，
但它們回答的是不同問題，決定是**都留著、角色分清楚，不合併**：

| | `pipeline.ingest.risk`（邏輯迴歸） | `pipeline.attribution.forecast`（水量平衡） |
|---|---|---|
| 回答什麼問題 | 這座湖現在危不危險？ | 已經被判定該關注的湖，大概還有多久可能溢流？ |
| 覆蓋範圍 | 清冊 71/75 筆自動評估 | 只有查得到公開可信集水面積來源的湖泊（目前僅花蓮馬太鞍溪 1 筆，見下） |
| 資料依據 | ERA5-Land 降雨再分析 + 登載蓄水量特徵 | DEM 水位–容積曲線 + 集水面積 + 降雨情境（Rational Method） |
| 可稽核性 | 統計模型，正樣本僅 12 筆、無留出驗證 | 確定性計算，每一步公式都可攤開檢查 |
| CAP 欄位 | **決定** `severity` / `urgency` / `certainty` | 附加在 `info.forecast` 與 CAP parameters，**不影響**上述三者 |

**決定：CAP 的 `severity`/`urgency`/`certainty` 一律只看 `risk.js`
（邏輯迴歸），不論 `forecast.js` 有沒有資料、預估結果是什麼，都不會
回頭覆寫這三個欄位。** 理由：邏輯迴歸雖然統計上不夠嚴謹（已誠實反映
在 `certainty` 最高只給 `Possible`），但它是唯一能自動覆蓋清冊裡
幾乎所有湖泊的信號；水量平衡預報方法上更可稽核，但需要集水面積這個
本專案沒有自動算出來的輸入（真實集水區向量圖資尚未整合，見
`pipeline/trigger/catchment.py` 的 TODO），若要求它覆蓋全部湖泊，
要嘛得對其餘 74 筆瞎猜集水面積、要嘛就只能服務少數幾筆——兩者都不該
拿來決定「該不該示警」這種需要全面覆蓋的判斷。

`pipeline.attribution.forecast_run` 因此故意設計成「查得到公開可信
來源就算、查不到就誠實回傳 None」，不猜數字充數（見該檔檔頭）。
目前唯一有結果的是花蓮馬太鞍溪——集水面積 63.23 km²（6,323 公頃）
引自農業部（農村水保署／林業及自然保育署）資料，經維基百科「花蓮
馬太鞍溪堰塞湖災害」條目轉引核實。這個數字是真的，但餵給它的水位–
容積曲線與壩頂高程仍是本次合成示範地形算出來的（見上方 assess 小節），
所以「預估溢流時間」本身仍只是示範 forecast.py 方法可用、可稽核，
不是可以真的拿來應變的預測——這個雙層限制（真集水面積、假地形）在
`forecast.js` 的 `disclaimer` 欄位與前端「溢流預報」卡片都會一路帶著，
不會被簡化成單一句「這是 demo」就交代過去。

前端（`dashboard/app.js`）把兩者的呈現位置刻意分開：判定依據卡片
（風險機率、severity 由來）跟溢流預報卡片是兩個獨立區塊，不會共用
同一組標題或合併成一段話，避免讓人以為兩個數字來自同一個模型。
