"""权重读取与校验。"""
from __future__ import annotations

from app.services.rules import get_rules

REQUIRED_COMPONENTS = (
    "journal", "citation", "timeliness", "author",
    "reproducibility", "relevance", "content_quality",
)


def get_weights() -> dict[str, float]:
    rules = get_rules()
    w = dict(rules.get("weights", {}))
    for k in REQUIRED_COMPONENTS:
        w.setdefault(k, 0.2)
    # 归一化
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}
