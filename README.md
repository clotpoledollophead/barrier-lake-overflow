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
| 溢流預報（水量平衡） | ✅ 核心完成，待接 QPF |
| 風險模型（ERA5-Land + 邏輯迴歸，係數與雨量來源採用 package 版本） | ✅ 71/75 筆有評估，CWA 即時／offline 佔位皆可 |
| CAP 示警輸出 | ✅ demo（`status=Test`），area 暫用 circle 頂著 |
| CWA API 介接 | ✅ 已接（見 `pipeline.ingest.cwa` / `risk`），SAR/DEM 仍待 |
| SAR 前處理與偵測 | ⬜ |
| DEM 量化與淹沒模擬 | ⬜ |

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
# 測試（47 項）
pytest

# 一鍵重建所有前端資料：清冊 → lakes.js、風險模型 → risk.js、成因敘述加註
python -m pipeline.build_all

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
│   │   ├── build_all.py       一鍵串接 ingest → risk → attribution
│   │   ├── ingest/            資料取得（清冊、CWA、CDSE）
│   │   ├── preprocess/        影像前處理          ⬜
│   │   ├── detect/            偵測                ⬜
│   │   ├── assess/            量化評估            ⬜
│   │   └── attribution/       成因歸因與溢流預報  ✅
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
哪放。`preprocess`、`detect`、`assess` 三個目錄下各有一份 README
說明預定內容。

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
`area` 也還沒有淹沒範圍模擬，暫用壩址座標＋固定半徑的 `circle` 頂著。
這些都須在正式對外前逐一處理，不能只改 `status` 就上線。

風險模型本身不知道湖體現況是否還存在——`cap.js` 以清冊 `statusKey`
把關：「已消失」一律 `urgency=Past`／`responseType=AllClear`，
「存在(已穩定)」則把模型判的高風險下修一級，只有「監測中」才直接
採信模型輸出。這層把關若拿掉，24 筆模型判高風險裡有 23 筆其實是
已消失或已穩定的湖，會是很嚴重的「狼來了」假警報。
