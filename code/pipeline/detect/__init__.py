"""
偵測層。

  water.py        — 水體萃取：SAR 低回波（Otsu）+ 光學 NDWI 雙軌
  landslide.py     — 崩塌變化偵測：振幅比值法（主力）+ 相干性變化法（升級）
  barrier_lake.py  — 堰塞湖判定：水體 × 河道相交 × 上游崩塌 × 多時相持續性，
                     輸出 A/B/C 信心分級
"""
