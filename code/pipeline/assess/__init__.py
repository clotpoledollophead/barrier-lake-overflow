"""
量化評估。

  hypsometry.py  — 由 DEM 對壩址上游做 priority-flood，建立水位–面積–容積曲線
                   （輸出格式與 attribution.forecast 的 LakeState.hypsometric 相容）
  inundation.py  — 淹沒模擬：bathtub model（v1，限制展開距離 2 km）；
                   簡化一維水動力為後續升級路徑
  exposure.py    — 暴露評估：人口網格加總、道路中斷長度、孤島化聚落（簡化代理）
"""
