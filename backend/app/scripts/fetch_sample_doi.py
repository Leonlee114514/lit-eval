"""CLI 端到端测试：抓取 + 指标计算，不落库。

用法：
    python -m app.scripts.fetch_sample_doi 10.1038/nature14539
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.config import get_settings
from app.services import metrics
from app.services.fetchers.base import FetcherOrchestrator


async def run(doi: str) -> None:
    settings = get_settings()
    orch = FetcherOrchestrator(mailto=settings.fetcher_mailto)
    try:
        data, trace, missing = await orch.fetch_by_doi(doi)

        # 构建轻量对象供指标计算（复用 Paper 字段名）
        class _P:
            pass

        p = _P()
        for k, v in data.items():
            setattr(p, k, v)
        p.subfield_id = data.get("subfield_id")
        p.field_id = data.get("field_id")  # 时效半衰期用
        p.cited_by_5yr = None
        p.impact_factor = None
        p.jcr_quartile = None
        p.cas_zone = None
        p.is_classic = False
        p.is_retracted = False
        p.has_data_availability_stmt = None  # 无可重复性证据 → 中性分
        p.has_code_repo = None
        p.has_supplement = None
        p.fetch_log = trace
        p.publication_year = data.get("publication_year")
        p.cited_by_count = data.get("cited_by_count")
        p.citation_percentile = data.get("citation_percentile")
        p.journal_2yr_mean = data.get("journal_2yr_mean")
        p.authors = data.get("authors") or []
        p.journal_percentile = None  # 与真实 Paper 同字段；本脚本不落库，留空由 journal.compute 判 missing

        # v5 活跃度：老文献按近5年被引同龄分位——补抓近5年被引才有意义
        p.cited_by_5yr = None
        if getattr(p, "openalex_work_id", None):
            try:
                from app.services.evaluation_service import _fetch_cited_5yr

                p.cited_by_5yr = await _fetch_cited_5yr(orch, p.openalex_work_id)
            except Exception as e:
                print(f"近5年被引查询失败: {e}")

        journal = metrics.journal.compute(p)
        citation = metrics.citation.compute(p)
        recency = metrics.recency.compute(p, cited_by_5yr=p.cited_by_5yr)
        author = metrics.author.compute(p)
        repro = metrics.reproducibility.compute(p, text=None)

        # 学科百分位
        from app.services import distributions

        percentile = distributions.percentile_in(
            distributions.journal_dist_for(p.subfield_id), p.journal_2yr_mean
        )
        print("=" * 60)
        print(f"DOI        : {doi}")
        print(f"标题       : {data.get('title')}")
        print(f"期刊       : {data.get('journal')} ({data.get('publication_year')})")
        print(f"学科       : {data.get('field_of_study')} [{data.get('subfield_id')}]")
        print(f"类型       : {data.get('work_type')}")
        print(f"被引总数   : {data.get('cited_by_count')}")
        print(f"同年百分位 : {data.get('citation_percentile')}")
        print(f"期刊2yr均值: {data.get('journal_2yr_mean')}")
        print(f"期刊百分位 : {percentile}")
        print(f"缺失字段   : {missing or '无'}")
        print("-" * 60)
        for name, res in [
            ("期刊权重", journal), ("被引权重", citation), ("活跃度权重", recency),
            ("作者权重", author), ("可重复性", repro),
        ]:
            print(f"{name}: {res['score']} | {res['basis']}")
        print("=" * 60)
        print("来源留痕:")
        for t in trace:
            print(f"  {t['source']}: ok={t['ok']} err={t['error'] or '-'} fields={len(t['fields'])}")
    finally:
        await orch.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m app.scripts.fetch_sample_doi <DOI>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))
