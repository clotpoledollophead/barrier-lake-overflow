# detect — 偵測

尚未實作。預定內容：

- `water.py` — 水體萃取：SAR 低回波（Otsu 自動門檻）+ 光學 NDWI
- `landslide.py` — 崩塌變化偵測：振幅比值法（GRD）為主力，
  相干性變化法（SLC）為升級
- `barrier_lake.py` — 堰塞湖判定：新增水體 × 河道相交 × 上游崩塌 ×
  多時相持續性，輸出 A/B/C 信心分級
