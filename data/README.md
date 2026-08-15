# data

```
data/
├── raw/       原始資料，小型 CSV 進版控
└── derived/   中繼產物，不進版控（可重新產生）
```

`derived/trigger_state.json` 由 `pipeline.trigger.service` 寫入，記住
「最近一次達門檻地震的時間與震央」，供跨輪次的複合加權判斷用
（見架構文件 §04「地震後 30 天內遇豪雨 → 門檻下修 20%」）。
刪掉這個檔案不會壞任何東西，只是複合加權會忘記歷史地震，
下次執行 `pipeline.trigger.service` 時重新建立即可。

## raw/

| 檔案 | 來源 | 說明 |
|---|---|---|
| `taiwan-barrier-lakes.csv` | 農業部農村發展及水土保持署 | 堰塞湖清冊，75 筆、1979–2026 |
| `risk/lake_risk_predictions.csv` | 外部建模流程輸出快照（**已停用**） | 舊版 ERA5-Land 批次預測，`pipeline.ingest.risk` 已改用 package 版模型，不再讀取；保留供對照 |
| `risk/risk_formula_coefs.csv` | 同上（**已停用**） | 邏輯迴歸係數；係數本身跟 package 版一致，但已改為寫死在 `pipeline/ingest/risk.py`，不再讀此檔 |
| `risk/feature_importance.csv` | 同上（**已停用**） | 決策樹特徵重要性；package 版模型沒有對應的決策樹，目前無人讀取 |
| `risk/decision_rules.txt` | 同上（**已停用**） | 決策樹規則文字版；同上，目前無人讀取 |
| `risk/metrics.json` | 同上（**已停用**） | 模型後設資料；`nPositives=12` 等數字已內建於 `pipeline/ingest/risk.py` |
| `dem/<lakeId>.tif`（不存在） | 尚未取得 | `pipeline.assess.run` 優先讀取的真實 DEM 路徑慣例（單一湖泊裁切好的 GeoTIFF），本專案目前沒有隨附。也接受 `dem/taiwan_dem.tif`（全台 DEM，用湖泊座標裁切）。兩者都不存在時，`build_all --demo-dem` 會退回 `pipeline.assess.synthetic_dem` 的合成地形（demo 用，非真實地形）。

清冊原始座標為 **TWD97 TM2（EPSG:3826）**，不是經緯度，直接畫會落到
非洲外海。轉換由 `code/pipeline/ingest/inventory.py` 處理。

清冊更新後重跑：

```bash
cd code
python -m pipeline.ingest.inventory \
    ../data/raw/taiwan-barrier-lakes.csv dashboard/data/lakes.js
python -m pipeline.attribution.annotate
```

風險模型更新後重跑（產生 CAP 示警要用的 `dashboard/data/risk.js`）：

```bash
cd code
export CWA_API_KEY="CWA-你的授權碼"    # opendata.cwa.gov.tw 免費申請
python -m pipeline.ingest.risk         # 沒設金鑰會自動退回 offline 佔位
```

模型公式、係數、平均值都採用 package（`make_risk_snapshot.py`）版本，
直接寫在 `pipeline/ingest/risk.py` 裡；雨量特徵抓 CWA 即時觀測，
不再依賴上面標「已停用」的那幾份外部建模流程輸出。那幾份檔案還留在
`risk/` 底下供對照／歸檔，確定不需要可自行刪除，本專案不會自動清掉。

`risk/` 底下的檔案都很小（快照，非時序），可以進版控。**但風險模型
的逐日特徵矩陣（如 `feature_panel.csv`，215,117 列、26MB）屬於中繼產物，
放在 `derived/` 不進版控**——這是可重跑重新產生的東西，不是快照本身。

## derived/

衛星影像、DEM、中繼運算結果、風險模型的逐日特徵矩陣。**一律不進版控**
——單景 Sentinel-1 動輒數 GB，GitHub 單檔上限 100 MB。
