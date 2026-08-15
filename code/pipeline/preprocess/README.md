# preprocess — 影像前處理

已實作（v1）：

- `sar.py` — Sentinel-1 IW GRD 前處理鏈：輻射校正（校正常數簡化式）
  → 去斑（中值濾波近似 Lee filter，可選）→ 地形校正（重投影對齊 DEM，
  非完整 RTC）。軌道精化仍交由 SNAP 產出的中繼檔案作為輸入。
- `mask.py` — 遮罩：坡度、雷達陰影（layover/shadow，依 DEM 局部入射角計算）

雷達陰影是山區水體萃取最大的坑：陡坡的低回波會被誤判為水體，
已用 DEM 計算局部入射角預先遮罩（見 `mask.layover_shadow_mask`）。

尚未整合：esa-snappy（完整 SNAP 校正鏈）、完整 RTC。
