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
| CWA API 介接 | ⬜ |
| SAR 前處理與偵測 | ⬜ |
| DEM 量化與淹沒模擬 | ⬜ |

---

## 目錄結構

```
ossint-2026/
├── code/
│   ├── pyproject.toml
│   ├── pipeline/              Python 套件，依資料流階段劃分
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
├── docs/                      架構報告、資料圖鑑、模組說明
├── people/                    各成員的 PROGRESS.md
├── SCHEDULE.md
├── requirements.txt
└── .gitignore
```

子套件按**管線階段**劃分而非技術類別，目的是讓人看目錄就知道東西該往
哪放。`preprocess`、`detect`、`assess` 三個目錄下各有一份 README
說明預定內容。

---

## 安裝

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd code && pip install -e .
```

`pip install -e .` 把 `pipeline` 裝成可編輯套件，之後任何位置都能
`from pipeline.attribution import ...`，不需要 `sys.path` 手動插入。

---

## 常用指令

全部在 `code/` 目錄下執行。

```bash
# 測試
pytest

# 清冊 CSV → 前端資料（座標轉換）
python -m pipeline.ingest.inventory \
    ../data/raw/taiwan-barrier-lakes.csv dashboard/data/lakes.js

# 為全清冊產生成因敘述，寫回 lakes.js
python -m pipeline.attribution.annotate

# 各模組的 doctest 與示範
python -m pipeline.attribution.verbalize
python -m pipeline.attribution.rules
python -m pipeline.attribution.forecast
python -m pipeline.attribution.compose
```

前端直接開 `code/dashboard/index.html` 即可，沒有建置步驟。

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