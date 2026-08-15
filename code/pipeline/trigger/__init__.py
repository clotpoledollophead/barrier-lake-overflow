"""
觸發層：門檻判斷與任務排程（架構文件 §04）。

對照 code-overview.md 的分層慣例，本套件負責「事件觸發式」架構最前端
的那一段：
    earthquake.py   地震資料擷取（CWA E-A0015-001 顯著有感地震報告）
    thresholds.py   純規則判斷（規模/震度/雨量門檻、複合加權），不碰網路
    catchment.py    集水區圈定（簡化版：地理半徑近似 + 已知湖泊清冊比對）
    dispatch.py     CDSE 影像調度（查詢可用 Sentinel-1 景、預估下次過境）
    tasking.py      把上述結果組成「監測任務」，供後續 preprocess/detect 使用
    service.py       輪詢入口（--once 單次檢查／--poll 常駐輪詢）

跟 ingest/attribution 的階段劃分原則一致：thresholds.py 只做判斷、
不夾帶任何 I/O，方便單元測試；會碰網路或檔案的部分（earthquake.py、
dispatch.py、service.py）都設計成「拿不到資料就降級，不讓整條管線掛掉」，
沿用 pipeline.ingest.cwa / pipeline.ingest.risk 已經用過的 offline 佔位模式。
"""
