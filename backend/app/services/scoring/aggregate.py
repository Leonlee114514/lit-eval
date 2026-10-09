"""评分聚合：七因子综合质量分 + 五维雷达分 + 置信度。"""
from __future__ import annotations

import logging
from typing import Any

from app.services.scoring.weights import get_weights

logger = logging.getLogger(__name__)

RADAR_KEYS = ["journal_level", "citation_impact", "content_quality", "timeliness", "relevance"]


def aggregate(
    component_scores: dict[str, float],
    radar_extra: dict[str, float],
    confidence: float,
) -> dict[str, Any]:
    """component_scores: 七路 0-100（journal/citation/timeliness/author/reproducibility/relevance/content_quality）。

    content_quality 兼容两种传法：
    - v6 正式路径：直接在 component_scores 里
    - 旧调用：从 radar_extra 读取（缺省按缺数据中性 50）
    """
    weights = get_weights()
    radar_extra = radar_extra or {}
    effective = dict(component_scores)
    if "content_quality" not in effective:
        effective["content_quality"] = radar_extra.get("content_quality", 50.0)

    composite = sum(effective.get(k, 0.0) * weights[k] for k in weights)

    radar = {
        "journal_level": effective.get("journal", 0.0),
        "citation_impact": effective.get("citation", 0.0),
        "timeliness": effective.get("timeliness", 0.0),
        "content_quality": effective.get("content_quality", 0.0),
        # v4：relevance 已是正式分量（radar_extra 传法向后兼容）
        "relevance": effective.get("relevance", radar_extra.get("relevance", 0.0)),
    }
    all_keys = set(component_scores) | set(weights)
    return {
        "composite_score": round(composite, 1),
        "weights": weights,
        "component_scores": {k: round(effective.get(k, 0.0), 1) for k in all_keys},
        "radar_scores": {k: round(v, 1) for k, v in radar.items()},
        "confidence": round(confidence, 2),
    }


def compute_confidence(paper, missing_fields: list[str]) -> float:
    """关键字段完整率（v6：全文可替代摘要作为文本可得性信号）。"""
    key_fields = [
        "title", "publication_year", "journal",
        "cited_by_count", "authors", "journal_2yr_mean",
    ]
    present = sum(1 for f in key_fields if getattr(paper, f, None) not in (None, "", []))
    # 摘要或全文至少其一 → 文本可得性达标（内容质量/可重复性才能真实评估）
    if getattr(paper, "abstract", None) or getattr(paper, "fulltext_path", None):
        present += 1
    total = len(key_fields) + 1
    return present / total
