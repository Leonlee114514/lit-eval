"""GNN / 引用网络分析接口。

2026-08-16 起正式实现已迁移到 `app.services.citation_network`（PageRank、
社区发现、文献耦合、教科书式引用识别），并挂到
`GET /api/papers/{paper_id}/citation-network`。

本文件保留为模块边界兼容占位，后续若引入全库级共引网络/引用价值预测，
可在 CitationGraph 上扩展。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CitationGraph:
    """全库级引用网络分析占位（单篇局部网络见 citation_network.py）。"""

    def __init__(self):
        logger.info("CitationGraph 全库级分析尚未实现；单篇网络见 services/citation_network")

    def add_paper(self, openalex_id: str, cited_by: list[str], references: list[str]) -> None:
        pass

    def pagerank(self) -> dict[str, float]:
        return {}
