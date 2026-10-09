"""跨测试文件共用的替身与工具。"""

from __future__ import annotations


def _force_coverage(monkeypatch):
    """强制相关性/热点走覆盖率路径（关 fastembed/SBERT），保证覆盖率/TF-IDF 测试确定性。

    fastembed 已默认启用（.env USE_FASTEMBED=1），但这些测试针对覆盖率/同义词/跨语/
    TF-IDF 兜底逻辑本身。同时 patch relevance 与 recency（recency._semantic_hot 也读 fastembed）。
    """
    from app.config import Settings
    from app.services.content import relevance as rel
    from app.services.metrics import recency as rec

    fake = lambda: Settings(use_fastembed=False, use_sbert=False)
    monkeypatch.setattr(rel, "get_settings", fake)
    monkeypatch.setattr(rec, "get_settings", fake)


class _MiniPaper:
    """模拟 Paper 的最小对象（字段子集）。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        for f in (
            "journal_2yr_mean", "impact_factor", "jcr_quartile", "cas_zone",
            "subfield_id", "field_of_study", "field_id", "citation_percentile",
            "cited_by_count", "cited_by_5yr", "publication_year", "is_classic",
            "is_retracted", "authors", "has_data_availability_stmt", "has_code_repo",
            "has_supplement", "work_type", "fetch_log", "doi", "journal_percentile",
            "title", "abstract", "fulltext_path",
        ):
            setattr(self, f, kw.get(f))
        if self.authors is None:
            self.authors = []
        if self.fetch_log is None:
            self.fetch_log = []
