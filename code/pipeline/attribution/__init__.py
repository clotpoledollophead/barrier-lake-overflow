"""
成因歸因與溢流預報（規則式，不使用 LLM）。

常用入口：
    from pipeline.attribution import attribute, describe

採延遲匯入（PEP 562），這樣 `python -m pipeline.attribution.rules`
執行單一模組時不會出現重複載入的警告。
"""

__all__ = [
    "attribute", "describe", "Composer", "Narrative",
    "Attribution", "LakeRecord", "Observations",
]

_LOCATIONS = {
    "attribute": "rules",
    "Attribution": "rules",
    "LakeRecord": "rules",
    "Observations": "rules",
    "describe": "compose",
    "Composer": "compose",
    "Narrative": "compose",
}


def __getattr__(name):
    module = _LOCATIONS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    return getattr(import_module(f".{module}", __name__), name)


def __dir__():
    return sorted(__all__)
