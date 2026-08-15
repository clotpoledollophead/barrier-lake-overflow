"""
影像前處理。

  sar.py  — Sentinel-1 IW GRD 前處理鏈：輻射校正 → 去斑（可選）→ 地形校正
  mask.py — 遮罩：坡度、雷達陰影（layover/shadow）

雷達陰影是山區水體萃取最大的坑：陡坡的低回波會被誤判為水體，
必須以 DEM 計算局部入射角預先遮罩，見 mask.layover_shadow_mask。
"""
