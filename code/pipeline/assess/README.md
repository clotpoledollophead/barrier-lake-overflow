# assess — 量化評估

已實作（v1）：

- `hypsometry.py` — 由 DEM 對壩址上游做 priority-flood（純 Python，
  不依賴 richdem），建立水位–面積–容積曲線；輸出格式與
  `attribution.forecast.LakeState.hypsometric` 直接相容
- `inundation.py` — 淹沒模擬：bathtub model，限制展開距離 2 km；
  簡化一維水動力（HEC-RAS 等）為後續升級路徑
- `exposure.py` — 暴露評估：人口網格加總、道路中斷長度（柵格近似，
  向量版需 geopandas）、孤島化聚落（簡化代理判定，非路網連通性分析）
- `synthetic_dem.py` — **展示用合成地形**（山谷 + 攔阻壩），本專案沒有
  隨附真實 DEM 檔案時的 demo 備援，蓄水量已校準至清冊登載數字，
  其餘地形參數（谷寬、坡度、壩高）皆為通用預設值、不是真實測量值。
  輸出一律標註 `method: "synthetic_demo_dem"`。
- `run.py` — 把 `hypsometry` + `inundation`（+ 有真實 DEM 時走檔案 I/O、
  沒有則用 `synthetic_dem` 頂著）串成「給一筆湖泊紀錄，吐出淹沒多邊形」
  的管線，只處理清冊 `statusKey == "watch"` 的湖泊，輸出
  `dashboard/data/inundation.js`（`window.LAKE_INUNDATION`）。
  由 `pipeline.build_all --demo-dem` 呼叫，`dashboard/cap.js` 會優先用
  這裡的多邊形取代固定半徑的 `circle`。

尚未整合：

- SEGIS 人口網格與水利署道路圖資的實際下載/柵格化管線（`exposure.py`
  目前吃已柵格化/對齊好的陣列，`run.py` 目前不呼叫 `exposure.py`——
  用合成地形去疊合成人口/道路，兩層假資料疊在一起不確定性會複合到
  失去意義，等真實資料到位再接）
- 真實 DEM（`data/raw/dem/<lakeId>.tif` 或 `data/raw/dem/taiwan_dem.tif`）
  ——`run.py` 的檔案 I/O 路徑已經寫好並重用 `hypsometry`/`inundation`
  既有測試過的函式，但本專案目前沒有隨附任何 GeoTIFF，這條路徑
  尚未被真的跑過一次

