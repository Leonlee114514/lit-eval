"""检索式 A/B 对比 CLI：raw 原样透传 vs expanded 同义词扩展。

用法：
    python -m app.scripts.compare_search_modes "waterborne polyurethane coating"
    python -m app.scripts.compare_search_modes "水性聚氨酯涂料" --per-page 20 --top 10
    python -m app.scripts.compare_search_modes "query A" "query B"

为什么需要它：SEARCH_QUERY_MODE 默认是 raw（行为与旧版一致），同义词扩展是双刃剑
—— 缩写类词（如 PU）容易引入噪声召回。所以在改 .env 默认开启之前，用这个脚本在
真实课题上比对"命中数"与"前 N 条的题目"，确认扩展确实变好再开。

命中数来自 OpenAlex 的 meta.count，与 per-page 无关；per-page 只决定能看多少条题目，
所以 --per-page 1 就能做最省流量的纯计数对比。
"""
from __future__ import annotations

import argparse
import asyncio

from app.services.content.relevance import _SYNONYMS
from app.services.fetchers import query_builder
from app.services.fetchers.openalex import OpenAlexFetcher

from app.config import get_settings


async def _probe(fetcher: OpenAlexFetcher, label: str, plan: query_builder.QueryPlan,
                 per_page: int, top: int) -> tuple[int | None, list[str]]:
    print(f"\n--- {label}（mode={plan.mode}）---")
    for i, v in enumerate(plan.variants, 1):
        print(f"    检索式[{i}/{len(plan.variants)}]: {v}")
    if plan.note:
        print(f"    说明: {plan.note}")

    if not plan.variants:
        print("    （空检索式，跳过）")
        return None, []

    if plan.is_multi:
        raw = await fetcher.search_works_union(plan.variants, per_page=per_page)
    else:
        raw = await fetcher.search_works(plan.variants[0], per_page=per_page)

    total = raw.get("total")
    est = "（估算：多段命中数之和，非并集真实值）" if raw.get("total_estimated") else ""
    print(f"    命中: {total}{est}")

    titles: list[str] = []
    for j, r in enumerate(raw.get("results", [])[:top], 1):
        title = (r.get("title") or "(无标题)")[:88]
        titles.append(title)
        print(
            f"    {j:2d}. [{r.get('publication_year') or '—'}] "
            f"被引 {r.get('cited_by_count')} | {(r.get('journal') or '—')[:34]} | {title}"
        )
    return total, titles


async def run(queries: list[str], per_page: int, top: int, extra_synonyms: str) -> None:
    settings = get_settings()
    fetcher = OpenAlexFetcher(
        mailto=settings.fetcher_mailto or "lit-eval@example.com",
        api_key=settings.openalex_api_key,
    )
    print(f"OpenAlex API key: {'已配置（10× 额度）' if settings.openalex_api_key else '未配置（走 mailto 认证档）'}")
    print(f"内置同义词表: {len(_SYNONYMS)} 条；附加同义词: {extra_synonyms or '（无）'}")

    try:
        for q in queries:
            raw_plan = query_builder.build_query_plan(q, mode="raw")
            exp_plan = query_builder.build_query_plan(
                q, mode="expanded", base_synonyms=_SYNONYMS, extra_synonyms=extra_synonyms
            )
            print(f"\n================ 课题: {q!r} ================")
            raw_total, _ = await _probe(fetcher, "raw 原样透传", raw_plan, per_page, top)
            exp_total, _ = await _probe(fetcher, "expanded 同义词扩展", exp_plan, per_page, top)

            if raw_total and exp_total:
                delta = exp_total - raw_total
                sign = "+" if delta >= 0 else ""
                pct = sign + f"{delta / raw_total * 100:.1f}%"
                print(f"\n    → 命中数变化: {raw_total} → {exp_total}（{sign}{delta}，{pct}）")
    finally:
        await fetcher.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="对比 raw 与 expanded 两种检索式构造")
    parser.add_argument("queries", nargs="+", help="一个或多个真实课题关键词")
    parser.add_argument("--per-page", type=int, default=20, help="每条检索式取多少条结果（默认 20；1 = 纯计数最省流量）")
    parser.add_argument("--top", type=int, default=20, help="打印前多少条题目用于人工判读（默认 20）")
    parser.add_argument("--extra-synonyms", default=None, help="覆盖 SEARCH_EXTRA_SYNONYMS，如 pu=polyurethane,coat=coating")
    args = parser.parse_args()

    extra = args.extra_synonyms
    if extra is None:
        extra = get_settings().search_extra_synonyms
    asyncio.run(run(args.queries, args.per_page, args.top, extra))


if __name__ == "__main__":
    main()
