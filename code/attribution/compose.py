#!/usr/bin/env python3
"""
compose.py — 敘述組裝

把 rules.py 判定出的「命中規則 + 填槽值」依 templates.yaml 的句型
與語序組裝成中文敘述。這裡沒有任何判斷邏輯——所有決定都在
rules.py 做完了，本模組只負責查表、填槽、串接。

輸出同時附帶 rules_fired，介面上點敘述即可展開看依據。

用法
----
    from rules import LakeRecord, Observations, attribute
    from compose import Composer

    comp = Composer("templates.yaml")
    result = comp.render(attribute(rec, obs))
    print(result.text)
    print(result.rules_fired)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import yaml
except ImportError:  # 沒裝 PyYAML 時給出明確指示，而非 traceback
    raise SystemExit("需要 PyYAML：pip install pyyaml")

from rules import Attribution


# ══════════════════════════════════════════
# 結果
# ══════════════════════════════════════════

@dataclass
class Narrative:
    text: str
    sentences: list = field(default_factory=list)   # 依語序的各段片語
    rules_fired: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)  # 缺槽而略過的規則

    def __str__(self) -> str:
        return self.text


# ══════════════════════════════════════════
# 組裝器
# ══════════════════════════════════════════

# 需要先查表換成中文的槽位：欄位名 → templates.yaml 中的查表路徑
LOOKUP_SLOTS = {
    "position_key":  ("trigger", "position_phrase", "position_phrase"),
    "grade_key":     ("rainfall", "grade", "grade"),
    "compare_key":   ("rainfall", "compare_phrase", "compare_phrase"),
    "scale_key":     ("formation", "scale", "scale"),
    "cause_key":     ("fate", "breach_cause", "cause"),
}

SLOT_RE = re.compile(r"\{(\w+)\}")


class Composer:
    def __init__(self, template_path: str = "templates.yaml"):
        with open(template_path, encoding="utf-8") as f:
            self.tpl = yaml.safe_load(f)
        self.order = self.tpl.get("order", [])

    # ── 取句型 ──────────────────────────
    def _lookup(self, dotted: str) -> Optional[str]:
        """以 'trigger.typhoon.with_distance' 這種路徑取出句型字串。"""
        node: Any = self.tpl
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, str) else None

    def _resolve_lookups(self, slots: dict) -> dict:
        """
        把 *_key 槽位換成中文片語。
        例：position_key='outer' → position_phrase='外圍環流'
        """
        out = dict(slots)
        for key, (section, table, target) in LOOKUP_SLOTS.items():
            val = slots.get(key)
            if val is None:
                continue
            phrase = (self.tpl.get(section, {}).get(table, {}) or {}).get(val)
            if phrase is not None:
                out[target] = phrase
        return out

    # ── 填槽 ────────────────────────────
    def _fill(self, template: str, slots: dict) -> Optional[str]:
        """
        填入槽位。任一槽位缺值就回 None——整句捨棄，
        絕不輸出「達 None mm」這種殘句。
        """
        missing = [m for m in SLOT_RE.findall(template) if slots.get(m) is None]
        if missing:
            return None
        return SLOT_RE.sub(lambda m: str(slots[m.group(1)]), template)

    # ── 主流程 ──────────────────────────
    def render(self, attr: Attribution, joiner: str = "，", end: str = "。") -> Narrative:
        slots = self._resolve_lookups(attr.slots)

        # 依語序分組：rules_fired 的順序不保證，語序由 order 決定
        by_section: dict = {sec: [] for sec in self.order}
        unresolved = []

        for rule in attr.rules_fired:
            section = rule.split(".")[0]
            template = self._lookup(rule)
            if template is None:
                unresolved.append(f"{rule}（句型不存在）")
                continue
            sentence = self._fill(template, slots)
            if sentence is None:
                unresolved.append(f"{rule}（槽位缺值）")
                continue
            by_section.setdefault(section, []).append(sentence)

        sentences = []
        for sec in self.order:
            sentences.extend(by_section.get(sec, []))

        text = (joiner.join(sentences) + end) if sentences else ""

        return Narrative(
            text=text,
            sentences=sentences,
            rules_fired=list(attr.rules_fired),
            unresolved=unresolved,
        )


# ══════════════════════════════════════════
# 便利函式
# ══════════════════════════════════════════

_default: Optional[Composer] = None


def describe(attr: Attribution, template_path: str = "templates.yaml") -> Narrative:
    """單次呼叫的便利包裝，會快取 Composer。"""
    global _default
    if _default is None:
        _default = Composer(template_path)
    return _default.render(attr)


if __name__ == "__main__":
    import json
    import sys

    from rules import LakeRecord, Observations, attribute

    # 以清冊 2025 年三筆為示範
    demo = LakeRecord(
        seq=71, name="花蓮馬太鞍溪", year=2025,
        county="花蓮縣", town="萬榮鄉", village="明利村",
        landmark="林田山第118林班", cause="颱風", event="薇帕颱風",
        formed="2025/7/21", duration="64", volume=9100.0,
        breach_date="2025/09/23", breach_cause="溢流沖刷",
        status="監測中", setting="林班地",
        dam_xy=(280340, 2621899), slide_xy=(280002, 2624183),
    )
    obs = Observations(
        typhoon_name="薇帕颱風", typhoon_distance_km=180.4,
        rain_24h_mm=460.0, rain_percentile=99.4,
    )

    result = describe(attribute(demo, obs))
    print(result.text)
    print()
    print(json.dumps({
        "rules_fired": result.rules_fired,
        "unresolved": result.unresolved,
    }, ensure_ascii=False, indent=2))
