# data

```
data/
├── raw/       原始資料，小型 CSV 進版控
└── derived/   中繼產物，不進版控（可重新產生）
```

## raw/

| 檔案 | 來源 | 說明 |
|---|---|---|
| `taiwan-barrier-lakes.csv` | 農業部農村發展及水土保持署 | 堰塞湖清冊，75 筆、1979–2026 |

清冊原始座標為 **TWD97 TM2（EPSG:3826）**，不是經緯度，直接畫會落到
非洲外海。轉換由 `code/pipeline/ingest/inventory.py` 處理。

清冊更新後重跑：

```bash
cd code
python -m pipeline.ingest.inventory \
    ../data/raw/taiwan-barrier-lakes.csv dashboard/data/lakes.js
python -m pipeline.attribution.annotate
```

## derived/

衛星影像、DEM、中繼運算結果。**一律不進版控**——單景 Sentinel-1
動輒數 GB，GitHub 單檔上限 100 MB。
