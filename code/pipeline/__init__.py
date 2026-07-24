"""
OSSInt 2026 · 堰塞湖快速評估管線

子套件依資料流階段劃分：
    ingest      資料取得（清冊、CWA、CDSE）
    preprocess  影像前處理（SNAP、遮罩）
    detect      偵測（水體萃取、崩塌變化）
    assess      量化評估（DEM、蓄水量、淹沒）
    attribution 成因歸因與溢流預報
"""

__version__ = "0.1.0"
