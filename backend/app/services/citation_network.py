"""GNN 引用网络分析（P2 第一个大项）。

在真实引用树之上构建局部引文子图，输出：
- PageRank 中心性：哪些文献是局部引用网络的信息枢纽
- 社区发现：引用网络内自然形成的主题群落
- 文献耦合：施引文献与本文的参考文献重叠（bibliographic coupling）
- 共被引支持：本文引用的文献也被多少篇施引文献同时引用
- 教科书式引用识别：高被引 + 高共被引支持 + 足够"年资"的奠基性文献

网络规模控制：OpenAlex 单篇端点查询（无列表配额压力）——只对 top 8 施引文献
补查其 referenced_works，与根文献的引用集合求交。结果缓存 7 天。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import httpx
import networkx as nx

from app.cache import cache
from app.models.paper import Paper
from app.services.citation_graph import get_citation_graph
from app.services.rules import get_rules

logger = logging.getLogger(__name__)

API = "https://api.openalex.org"
_SEMAPHORE = asyncio.Semaphore(4)


async def get_citation_network(paper: Paper, mailto: str = "") -> dict | None:
    """构建单篇论文的局部 GNN 引用网络；无 OpenAlex 标识返回 None。"""
    work_id = paper.openalex_work_id
    if not work_id:
        return None

    rules = get_rules()
    cfg = rules.get("citation_network", {})
    citing_limit = int(cfg.get("citing_limit", 8))
    reference_limit = int(cfg.get("reference_limit", 8))
    base = await get_citation_graph(paper, mailto, citing_limit=citing_limit, reference_limit=reference_limit)
    if not base:
        return None

    root_ref_ids = await _get_referenced_works(work_id, mailto)
    citing_nodes = [n for n in base.get("citing", []) if n.get("id")]
    if citing_nodes:
        results = await asyncio.gather(
            *[_get_referenced_works(n["id"], mailto) for n in citing_nodes],
            return_exceptions=True,
        )
    else:
        results = []

    citing_ref_sets: dict[str, set[str]] = {}
    for node, res in zip(citing_nodes, results):
        if isinstance(res, set):
            citing_ref_sets[node["id"]] = res

    return _build_network(base, root_ref_ids, citing_ref_sets, cfg)


async def _get_referenced_works(openalex_id: str, mailto: str) -> set[str]:
    """取一篇 OpenAlex work 的完整 referenced_works（缓存 7 天）。"""
    cache_key = f"refs:{openalex_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return set(cached)

    async with _SEMAPHORE:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(
                    f"{API}/works/{openalex_id}",
                    params={"select": "referenced_works", "mailto": mailto},
                )
                resp.raise_for_status()
                refs = resp.json().get("referenced_works", []) or []
        except httpx.HTTPError as e:
            logger.warning("引用网络取 references 失败 %s: %s", openalex_id, e)
            return set()

    cache.set(cache_key, refs, ttl=7 * 24 * 3600)
    return set(refs)


def _node(
    node_id: str,
    title: str | None,
    cited_by_count: int | None,
    publication_year: int | None,
    role: str,
) -> dict:
    return {
        "id": node_id,
        "title": title,
        "cited_by_count": cited_by_count or 0,
        "publication_year": publication_year,
        "role": role,
    }


def _build_network(
    base: dict,
    root_ref_ids: set[str],
    citing_ref_sets: dict[str, set[str]],
    cfg: dict,
) -> dict:
    """纯函数：由引用树 + 各施引文献引用集合构建局部引文网络。"""
    root = base["root"]
    root_id = root.get("id") or "root"
    references = [n for n in base.get("references", []) if n.get("id")]
    citing = [n for n in base.get("citing", []) if n.get("id")]

    ref_ids = {n["id"] for n in references}
    nodes: list[dict] = []
    by_id: dict[str, dict] = {}

    nodes.append(_node(root_id, root.get("title"), root.get("cited_by_count"),
                       root.get("publication_year"), "root"))
    by_id[root_id] = nodes[-1]

    for n in references:
        nodes.append(_node(n["id"], n.get("title"), n.get("cited_by_count"),
                           n.get("publication_year"), "reference"))
        by_id[n["id"]] = nodes[-1]
    for n in citing:
        nodes.append(_node(n["id"], n.get("title"), n.get("cited_by_count"),
                           n.get("publication_year"), "citing"))
        by_id[n["id"]] = nodes[-1]

    # 有向引用边：施引文献 -> 被引文献（PageRank 沿引用方向流动到"被依赖"的文献）
    edges: list[dict] = []
    for c in citing:
        edges.append({"source": c["id"], "target": root_id, "type": "cites", "weight": 1})
        refs_of_citing = citing_ref_sets.get(c["id"], set())
        for ref in references:
            if ref["id"] in refs_of_citing:
                edges.append({"source": c["id"], "target": ref["id"], "type": "cites", "weight": 1})
    for ref in references:
        edges.append({"source": root_id, "target": ref["id"], "type": "cites", "weight": 1})

    # 节点级信号
    for c in citing:
        cid = c["id"]
        shared = len((citing_ref_sets.get(cid, set()) & root_ref_ids) & ref_ids)
        by_id[cid]["bibliographic_coupling"] = shared
        by_id[cid]["co_cited_count"] = 0
    by_id[root_id]["bibliographic_coupling"] = 0
    by_id[root_id]["co_cited_count"] = 0

    for ref in references:
        rid = ref["id"]
        co_count = 0
        for c in citing:
            if rid in citing_ref_sets.get(c["id"], set()):
                co_count += 1
        by_id[rid]["co_cited_count"] = co_count
        by_id[rid]["bibliographic_coupling"] = 0

    # PageRank（有向引文图）
    try:
        dg = nx.DiGraph()
        dg.add_nodes_from(by_id.keys())
        dg.add_weighted_edges_from(
            (e["source"], e["target"], e.get("weight", 1)) for e in edges
        )
        pr = nx.pagerank(dg, alpha=0.85, weight="weight")
    except Exception as e:  # 极端退化网络（无节点/自环异常）不阻断
        logger.warning("PageRank 计算失败: %s", e)
        pr = {nid: 0.0 for nid in by_id}

    for nid, nd in by_id.items():
        nd["pagerank"] = round(float(pr.get(nid, 0.0)), 6)

    # 社区发现（无向：引用 + 文献耦合加权）
    try:
        ug = nx.Graph()
        ug.add_nodes_from(by_id.keys())
        for e in edges:
            w = e.get("weight", 1)
            if e["source"] == root_id or e["target"] == root_id:
                # 根文献与施引文献的边，用文献耦合强度增强群落信号
                if e["source"] != root_id:
                    cid = e["source"]
                    w = 1 + int(by_id.get(cid, {}).get("bibliographic_coupling", 0))
            ug.add_edge(e["source"], e["target"], weight=w)
        comms = nx.community.greedy_modularity_communities(ug, weight="weight")
        community_of: dict[str, int] = {}
        for idx, members in enumerate(comms):
            for m in members:
                community_of[m] = idx
    except Exception as e:
        logger.warning("社区发现失败: %s", e)
        community_of = {nid: 0 for nid in by_id}

    # 教科书式引用识别
    min_cites = int(cfg.get("textbook_min_citations", 500))
    min_co = int(cfg.get("textbook_min_co_cited", 2))
    min_age = int(cfg.get("textbook_min_age", 10))
    this_year = date.today().year
    textbook_nodes: list[dict] = []
    for ref in references:
        nd = by_id[ref["id"]]
        age = (this_year - ref["publication_year"]) if ref.get("publication_year") else None
        is_textbook = bool(
            age is not None
            and age >= min_age
            and nd["cited_by_count"] >= min_cites
            and nd["co_cited_count"] >= min_co
        )
        nd["is_textbook"] = is_textbook
        if is_textbook:
            textbook_nodes.append(nd)

    for nd in nodes:
        nd["community"] = int(community_of.get(nd["id"], 0))

    ordered_pr = sorted(by_id.values(), key=lambda n: n["pagerank"], reverse=True)
    root_rank = next((i + 1 for i, n in enumerate(ordered_pr) if n["id"] == root_id), len(ordered_pr))
    top_central = [
        {
            "title": n["title"],
            "role": n["role"],
            "pagerank": n["pagerank"],
            "community": n["community"],
        }
        for n in ordered_pr
        if n["id"] != root_id
    ][:5]

    return {
        "root": by_id[root_id],
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "community_count": len({n["community"] for n in nodes}),
            "root_pagerank": by_id[root_id]["pagerank"],
            "root_pagerank_rank": root_rank,
            "top_central": top_central,
            "textbook_citations": textbook_nodes,
            "citing_with_bibliographic_coupling": sorted(
                [
                    {
                        "title": nd["title"],
                        "shared_references": nd.get("bibliographic_coupling", 0),
                        "pagerank": nd["pagerank"],
                    }
                    for nd in by_id.values()
                    if nd["role"] == "citing" and nd.get("bibliographic_coupling", 0) > 0
                ],
                key=lambda x: x["shared_references"],
                reverse=True,
            ),
            "co_cited_references": sorted(
                [
                    {
                        "title": nd["title"],
                        "co_cited_count": nd.get("co_cited_count", 0),
                        "is_textbook": nd.get("is_textbook", False),
                    }
                    for nd in by_id.values()
                    if nd["role"] == "reference" and nd.get("co_cited_count", 0) > 0
                ],
                key=lambda x: x["co_cited_count"],
                reverse=True,
            ),
        },
    }
