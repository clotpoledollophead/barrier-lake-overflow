#!/usr/bin/env python3
"""
barrier_lake.py — 堰塞湖判定：水體 × 河道相交 × 上游崩塌 × 多時相持續性

單一時相的水體遮罩不能直接當堰塞湖：山區反光的水塘、農塘、雲影誤判、
既有水庫都會被 detect.water 判成「水體」。本檔把水體結果收斂成
「新生、位於河道上、上游有崩塌成因、且不是一次性雜訊」的堰塞湖判定，
並依證據強度分 A/B/C 三級——寧可分級讓人複核，也不要用單一布林值
假裝「是」或「不是」。

信心分級
--------
A（高，SAR+光學雙軌互證）
    同一時窗內 SAR 與光學兩軌皆判為水體（intersection 非空），
    且滿足河道相交與上游崩塌條件。誤判率最低，可直接進入
    assess 量化評估與 CAP 示警。

B（中，SAR 多時相持續）
    光學因雲層缺席（IW GRD 是雲層之上僅有的資料），但同一水體範圍
    在連續 ≥ MIN_PERSIST_PASSES 次 SAR 過境中都判為水體
    （排除單次雜訊、排除颱風期間暫時性淹水），且滿足河道相交與
    上游崩塌條件。可進入評估，但示警文字應註明「待光學或現地複核」。

C（低，單次或條件不全）
    僅單次 SAR 判定、或河道相交/上游崩塌其中之一不滿足。
    列入觀察清單，觸發下一次影像調度，但不足以做量化評估或示警。

河道相交與上游崩塌的判斷都容許緩衝距離（buffer_px），
因為 detect.water/landslide 的像元邊界本身就有若干像元的不確定性，
過度嚴格的「恰好相交」反而會漏掉真正的堰塞湖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.ndimage import binary_dilation, label as nd_label, find_objects

MIN_PERSIST_PASSES = 2   # Track B 最少連續過境次數


@dataclass
class BarrierLakeCandidate:
    """一個連通水體物件的判定結果。"""
    label_id: int
    area_ha: float
    on_river: bool
    upstream_landslide: bool
    n_sar_passes_persistent: int   # 該範圍在連續 SAR 過境中被判水體的次數
    has_optical_confirmation: bool
    confidence: str                # "A" / "B" / "C"
    reasons: list = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        """A/B 級才進入量化評估與示警；C 級僅列觀察。"""
        return self.confidence in ("A", "B")


def _dilate(mask: np.ndarray, buffer_px: int) -> np.ndarray:
    if buffer_px <= 0:
        return mask
    return binary_dilation(mask, iterations=buffer_px)


def classify(
    water_union: np.ndarray,
    water_intersection: Optional[np.ndarray],
    river_mask: np.ndarray,
    landslide_mask: np.ndarray,
    persist_count_map: Optional[np.ndarray] = None,
    cellsize: float = 10.0,
    buffer_px: int = 2,
    min_persist_passes: int = MIN_PERSIST_PASSES,
) -> list[BarrierLakeCandidate]:
    """
    對 water_union 裡每個連通水體物件做判定。

    參數
    ----
    water_union          — detect.water.fuse() 的 "union"（SAR∪光學，範圍最大）
    water_intersection    — detect.water.fuse() 的 "intersection"；None 表示
                           該時窗沒有可用光學影像（全雲或未調度）
    river_mask            — 河川圖資柵格化後的遮罩（水利署河道中心線緩衝）
    landslide_mask         — detect.landslide 的崩塌變化遮罩（應為上游影像疊圖後的結果，
                           即只保留水體物件「上游方向」的崩塌，此篩選建議在
                           呼叫本函式前，用集水區圖層先做過，本函式只檢查相交）
    persist_count_map     — 每像元「連續幾次 SAR 過境判為水體」的計數柵格；
                           None 表示沒有多時相資料，Track B 一律判不成立
    buffer_px             — 河道相交/崩塌相交的容許緩衝像元數

    回傳每個連通水體物件的 BarrierLakeCandidate 清單，依面積由大到小排序。
    """
    labels, n = nd_label(water_union)
    river_buf = _dilate(river_mask, buffer_px)
    slide_buf = _dilate(landslide_mask, buffer_px)

    candidates = []
    for obj_id in range(1, n + 1):
        obj_mask = labels == obj_id
        area_ha = float(obj_mask.sum()) * (cellsize ** 2) / 10_000.0

        on_river = bool((obj_mask & river_buf).any())
        upstream_slide = bool((_dilate(obj_mask, buffer_px) & slide_buf).any())

        has_optical = False
        if water_intersection is not None:
            has_optical = bool((obj_mask & water_intersection).any())

        n_persist = 0
        if persist_count_map is not None:
            vals = persist_count_map[obj_mask]
            if vals.size:
                n_persist = int(np.max(vals))

        reasons = []
        if on_river:
            reasons.append("位於河道緩衝範圍內")
        else:
            reasons.append("未與河道圖資相交")
        if upstream_slide:
            reasons.append("鄰近有崩塌變化偵測")
        else:
            reasons.append("鄰近無崩塌變化偵測")

        if on_river and upstream_slide and has_optical:
            confidence = "A"
            reasons.append("SAR 與光學雙軌互證")
        elif on_river and upstream_slide and n_persist >= min_persist_passes:
            confidence = "B"
            reasons.append(f"連續 {n_persist} 次 SAR 過境皆判為水體，待光學/現地複核")
        else:
            confidence = "C"
            if not (on_river and upstream_slide):
                reasons.append("河道相交或上游崩塌條件不全，列入觀察")
            else:
                reasons.append("僅單次判定或多時相資料不足，列入觀察")

        candidates.append(BarrierLakeCandidate(
            label_id=obj_id,
            area_ha=area_ha,
            on_river=on_river,
            upstream_landslide=upstream_slide,
            n_sar_passes_persistent=n_persist,
            has_optical_confirmation=has_optical,
            confidence=confidence,
            reasons=reasons,
        ))

    candidates.sort(key=lambda c: -c.area_ha)
    return candidates


def summarize(candidates: list[BarrierLakeCandidate]) -> str:
    """一行摘要，供 CLI 與 log 使用。"""
    counts = {"A": 0, "B": 0, "C": 0}
    for c in candidates:
        counts[c.confidence] += 1
    total_area = sum(c.area_ha for c in candidates)
    return (f"共 {len(candidates)} 個水體物件（A級 {counts['A']}、"
            f"B級 {counts['B']}、C級 {counts['C']}）｜總面積 {total_area:.2f} ha")


if __name__ == "__main__":
    # 示範：合成一個簡單場景驗證分級邏輯
    shape = (30, 30)
    water = np.zeros(shape, dtype=bool)
    water[10:15, 10:15] = True       # 候選水體 1：河道上、有崩塌、雙軌互證
    water[20:22, 20:22] = True       # 候選水體 2：孤立農塘，不在河道上

    intersection = np.zeros(shape, dtype=bool)
    intersection[10:15, 10:15] = True

    river = np.zeros(shape, dtype=bool)
    river[:, 12] = True              # 一條南北向河道穿過候選水體 1

    landslide = np.zeros(shape, dtype=bool)
    landslide[5:9, 10:15] = True     # 候選水體 1 上游有崩塌

    persist = np.zeros(shape, dtype=int)
    persist[10:15, 10:15] = 3
    persist[20:22, 20:22] = 1

    results = classify(water, intersection, river, landslide, persist_count_map=persist)
    print(summarize(results))
    for c in results:
        print(f"  物件{c.label_id}｜{c.area_ha:.2f} ha｜信心 {c.confidence}｜{'; '.join(c.reasons)}")
