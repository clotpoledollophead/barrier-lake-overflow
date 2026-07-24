# attribution — 成因歸因與溢流預報

> 原始碼位置：`code/pipeline/attribution/`

規則式（rule-based）的歸因判定與中文敘述生成，**不使用 LLM**。

災防系統的輸出必須可稽核：每一句話都要能追到是哪條規則、哪個門檻
產生的。純模板法不是退而求其次，是這個場域的正確工程選擇。

---

## 分層

```
觀測資料 ──▶ rules.py ──▶ Attribution ──▶ compose.py ──▶ 中文敘述
             （判定）      規則+槽位        （組裝）      + rules_fired
                                              ▲
                                     templates.yaml
                                       （句型庫）
```

| 檔案 | 職責 | 誰會改 |
|---|---|---|
| `rules.py` | 門檻判定與計算，決定「命中哪些規則」 | 工程師 |
| `verbalize.py` | 數值轉中文（時長、雨量級距、規模） | 工程師 |
| `templates.yaml` | 句型庫，決定「話怎麼說」 | **領域專家可直接改** |
| `compose.py` | 查表、填槽、依語序串接。**無判斷邏輯** | 少動 |
| `forecast.py` | 水量平衡與溢流時間區間 | 工程師 |
| `annotate.py` | 批次跑全清冊，把敘述寫回 `dashboard/data/lakes.js` | — |
| `../../tests/test_attribution.py` | 單元測試（39 項） | 工程師 |

把 `templates.yaml` 獨立出來很重要——水保署或災防領域的人可以直接
指出「這句話在專業上不成立」並自行修訂，不需要碰程式。這是黑箱
模型做不到的溝通方式。

---

## 三條不可違反的規則

**1. 缺槽即棄句。** 任一槽位是 `None`，整句捨棄，絕不輸出
「達 None mm」這種殘句。`compose.Composer._fill()` 強制執行。

**2. 「無」與「未記載」必須區分。** 「無潰決紀錄」和「潰決原因未載」
在災防判讀上是兩件事。見 `verbalize.absence()`。

**3. 沒資料就不說話。** 沒有雨量資料時不得憑空生出雨量數字；
颱風中心距離未知時不得硬套「外圍環流」。有測試守著這兩點。

---

## 用法

全部於 `code/` 目錄下執行：

```bash
# 單元測試（39 項）
pytest

# 各模組的 doctest 與示範
python -m pipeline.attribution.verbalize
python -m pipeline.attribution.rules
python -m pipeline.attribution.forecast
python -m pipeline.attribution.compose

# 批次加註全清冊 → dashboard/data/lakes.js 多出 narrative 與 rulesFired
python -m pipeline.attribution.annotate
```

程式內呼叫：

```python
from pipeline.attribution import LakeRecord, Observations, attribute, describe

rec = LakeRecord(seq=71, name="花蓮馬太鞍溪", cause="颱風",
                 event="薇帕颱風", duration="64", volume=9100.0,
                 status="監測中", landmark="林田山第118林班")
obs = Observations(typhoon_name="薇帕颱風", typhoon_distance_km=180.4,
                   rain_24h_mm=460.0, rain_percentile=99.4)

result = describe(attribute(rec, obs))
print(result.text)
print(result.rules_fired)   # 稽核用
```

輸出：

> 本堰塞湖形成於薇帕颱風外圍環流影響期間，颱風中心最近距離約 180 公里，
> 上游集水區 24 小時累積雨量達 460 mm，達大豪雨標準（為該站有紀錄以來
> 前 1% 之強降雨），觸發林田山第118林班崩塌，堵塞馬太鞍溪，崩塌地與壩體
> 相距約 2,309 公尺，形成蓄水量約 9,100 萬立方公尺之極大型堰塞湖，
> 自形成迄今 64 日（約 2.1 個月），現況列為監測中。

同一筆若不給 `obs`，會自動縮短為不含觀測的版本——不會臆測。

---

## 門檻一覽

對外簡報時須註明哪些是官方定義、哪些是專案自訂。

| 項目 | 門檻 | 來源 |
|---|---|---|
| 雨量分級 | 大雨 80／豪雨 200／大豪雨 350／超大豪雨 500 mm（24h） | **中央氣象署** |
| 颱風直接侵襲 | 中心距離 < 100 km | 專案自訂 |
| 颱風外圍環流 | 100–300 km | 專案自訂 |
| 西南氣流 | > 300 km 且西南風場顯著 | 專案自訂 |
| 地震關聯 | 形成於地震後 72 小時內、規模 ≥ 5.0 | 專案自訂 |
| 規模分級 | 小型 <100／中型 <1000／大型 <5000／極大型 ≥5000 萬 m³ | 專案自訂 |

---

## 溢流預報

```
剩餘容量 = V(壩頂高程) − V(當前水位)      ← 水位–容積曲線，由 DEM 填洼建立
淨入流   = C · i · A − 滲流 − 蒸發        ← 合理化公式
溢流時間 = 剩餘容量 / 淨入流
```

三個工程紀律，都寫進 `forecast.py` 並有測試：

- **一律輸出區間。** 高／中／低三個雨量情境各算一次，回傳最早、
  中位、最晚。「38 小時後溢流」這種假精確在災防上是危險的。
- **逕流係數必須外顯。** 震後裸露坡面可從 0.5 跳到 0.8 以上，這是
  最大的不確定來源。`Forecast.assumptions` 隨每次輸出揭露。
- **不外推超過預報時距。** 超出就回報「時距內不致溢流」，不硬算。

情境的雨量應取自 CWA 的 **QPF（定量降水預報）**，不是 QPE（觀測估計）
——預報與現況分析用的是不同產品，這是介接時最容易搞錯的地方。

### 必須揭露的限制

Sentinel-1 約 6 日重訪，兩次觀測之間水位是**純靠雨量外推、沒有實測
校正**。這是整個預報能力最脆弱的環節，`templates.yaml` 的
`forecast.disclaimer` 會隨每次輸出附上這句話。緩解方案是介接現地
水位計，或以上游雨量站作代理指標加密更新。

另外 DEM 是事件前地形，壩體形成後河床已改變、湖區也會持續淤積，
蓄水量估算會隨時間漂移——每次有新影像時要重新校正曲線。

---

## 尚未介接

`annotate.py` 的 `load_observations()` 目前回傳 `None`。未來的實作應放在 `pipeline/ingest/cwa.py`，介接以下資料源
後填入 `Observations` 即可，**敘述邏輯完全不用改**：

- CWA 自動雨量站歷史資料 → `rain_24h_mm` / `rain_max_hourly_mm`
- CWA 颱風資料庫路徑 → `typhoon_name` / `typhoon_distance_km`
- CWA 地震報告與測站震度 → `quake_time` / `quake_magnitude` / `pga_gal`

---

## 為什麼三筆 2025 年紀錄是好測試

| | 草嶺清水溪 | 馬太鞍溪 | 燕子口 |
|---|---|---|---|
| 蓄水量 | 1,400 萬 m³ | 9,100 萬 m³ | 190 萬 m³ |
| 存續 | **2 日** | **64 日** | 9 日 |
| 潰決原因 | 溢流沖刷 | 溢流沖刷 | **機具開挖** |

前兩者同為颱風型、同為溢流潰決，存續卻差三十倍——敘述必須能區分，
否則模板太粗糙。第三筆是人為介入，絕不可誤述為自然溢流。這三點都有
測試守著。
