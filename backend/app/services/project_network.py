"""项目级 GNN：把项目内全部论文投影到共享参考文献网络上。

思路：
1. 对项目内每篇论文取完整 referenced_works（单篇端点，已有 7 天缓存）
2. 统计每篇参考文献被项目内多少篇论文引用（共被引频率）
3. 只保留"共享参考文献"（≥2 篇论文引用），批量取元数据
4. 构建两层网络：
   - 引用子图：项目论文 → 共享参考文献（有向）
   - 文献耦合投影：项目论文之间共享参考文献（无向加权）
5. 输出：核心参考文献、论文 PageRank 排序、主题社区、教科书式引用候选

结果缓存 1 小时（refs 缓存 7 天，这里只缓存组装结果，避免项目论文变动后长期脏读）。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date

import httpx
import networkx as nx

from app.cache import cache
from app.config import get_settings
from app.services.citation_network import _get_referenced_works
from app.services.rules import get_rules

logger = logging.getLogger(__name__)

API = "https://api.openalex.org"
_SELECT = "id,display_name,cited_by_count,publication_year"
_BATCH = 50


async def get_project_network(project_id: str, papers: list, mailto: str = "") -> dict:
    """构建项目级 GNN；项目内有效 OpenAlex work 少于 2 时返回 None。"""
    rules = get_rules()
    cfg = rules.get("project_network", {})
    cache_key = f"pnet:{project_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    valid = [p for p in papers if getattr(p, "openalex_work_id", None)]
    if len(valid) < 2:
        return None

    # 1. 每篇项目论文的完整参考文献集合
    results = await asyncio.gather(
        *[_get_referenced_works(p.openalex_work_id, mailto) for p in valid],
        return_exceptions=True,
    )
    paper_refs: dict[str, set[str]] = {}
    for paper, res in zip(valid, results):
        if isinstance(res, set):
            paper_refs[paper.id] = res

    # 2. 统计参考文献被多少篇项目论文引用
    ref_papers: dict[str, set[str]] = defaultdict(set)
    for paper_id, refs in paper_refs.items():
        for rid in refs:
            ref_papers[rid].add(paper_id)

    min_co = int(cfg.get("min_co_cited", 2))
    max_refs = int(cfg.get("max_references", 60))
    shared_ids = [
        rid for rid, papers_set in ref_papers.items()
        if len(papers_set) >= min_co
    ]
    shared_ids.sort(key=lambda rid: (-len(ref_papers[rid]), rid))
    shared_ids = shared_ids[:max_refs]

    if not shared_ids:
        return None

    # 3. 共享参考文献元数据（批量列表查询，每批 50；失败跳过不阻断）
    ref_meta = await _fetch_works_meta(shared_ids, mailto)

    papers_meta = []
    for p in valid:
        papers_meta.append({
            "id": p.id,
            "openalex_id": p.openalex_work_id,
            "title": p.title,
            "cited_by_count": p.cited_by_count or 0,
            "publication_year": p.publication_year,
        })

    network = _build_project_network(papers_meta, ref_meta, paper_refs, ref_papers, cfg)
    cache.set(cache_key, network, ttl=3600)
    return network


async def _fetch_works_meta(openalex_ids: list[str], mailto: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(openalex_ids), _BATCH):
        chunk = openalex_ids[i : i + _BATCH]
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers=get_settings().openalex_headers
            ) as client:
                resp = await client.get(
                    f"{API}/works",
                    params={
                        "filter": f"openalex_id:{'|'.join(chunk)}",
                        "per-page": _BATCH,
                        "select": _SELECT,
                        "mailto": mailto,
                    },
                )
                resp.raise_for_status()
                for w in resp.json().get("results", []):
                    if w.get("display_name"):
                        out[w["id"]] = {
                            "id": w["id"],
                            "title": w["display_name"],
                            "cited_by_count": w.get("cited_by_count") or 0,
                            "publication_year": w.get("publication_year"),
                        }
        except httpx.HTTPError as e:
            logger.warning("项目网络批量查 works 失败: %s", e)
    return out


def _norm(values: dict[str, float]) -> dict[str, float]:
    mx = max(values.values()) or 1.0
    return {k: v / mx for k, v in values.items()}


def _build_project_network(
    papers_meta: list[dict],
    ref_meta: dict[str, dict],
    paper_refs: dict[str, set[str]],
    ref_papers: dict[str, set[str]],
    cfg: dict,
) -> dict:
    """纯函数：项目论文 + 共享参考文献 → 共引网络。"""
    paper_ids = {p["id"] for p in papers_meta}
    shared_refs = {
        rid: meta for rid, meta in ref_meta.items()
        if len(ref_papers.get(rid, set())) >= int(cfg.get("min_co_cited", 2))
    }

    nodes: list[dict] = []
    by_id: dict[str, dict] = {}
    for p in papers_meta:
        nd = {
            "id": p["id"],
            "title": p["title"],
            "cited_by_count": p["cited_by_count"],
            "publication_year": p["publication_year"],
            "role": "project_paper",
            "openalex_id": p["openalex_id"],
        }
        nodes.append(nd)
        by_id[p["id"]] = nd
    for rid, meta in shared_refs.items():
        nd = {
            "id": rid,
            "title": meta["title"],
            "cited_by_count": meta["cited_by_count"],
            "publication_year": meta["publication_year"],
            "role": "reference",
            "openalex_id": rid,
        }
        nodes.append(nd)
        by_id[rid] = nd

    # 有向引用边：项目论文 -> 参考文献
    edges: list[dict] = []
    coupling_pairs: dict[tuple[str, str], int] = defaultdict(int)
    for p in papers_meta:
        refs = (paper_refs.get(p["id"], set())) & set(shared_refs)
        for rid in refs:
            edges.append({"source": p["id"], "target": rid, "type": "cites", "weight": 1})

    # 文献耦合投影：论文-论文共享参考文献数
    paper_list = list(papers_meta)
    for i in range(len(paper_list)):
        for j in range(i + 1, len(paper_list)):
            a, b = paper_list[i], paper_list[j]
            shared = len(
                (paper_refs.get(a["id"], set()) & paper_refs.get(b["id"], set())) & set(shared_refs)
            )
            if shared >= int(cfg.get("coupling_min_shared", 1)):
                key = tuple(sorted((a["id"], b["id"])))
                coupling_pairs[key] = shared
                edges.append({
                    "source": a["id"], "target": b["id"], "type": "coupling", "weight": shared,
                })

    # 参考节点信号
    for nd in nodes:
        if nd["role"] == "reference":
            co = len(ref_papers.get(nd["id"], set()) & paper_ids)
            nd["co_cited_count"] = co
        else:
            nd["co_cited_count"] = 0

    # 参考文献 PageRank（有向引用图 paper->reference）
    try:
        dg = nx.DiGraph()
        dg.add_nodes_from(by_id.keys())
        dg.add_weighted_edges_from(
            (e["source"], e["target"], e.get("weight", 1))
            for e in edges if e["type"] == "cites"
        )
        ref_pr = nx.pagerank(dg, alpha=0.85, weight="weight")
    except Exception as e:
        logger.warning("项目网络 PageRank 失败: %s", e)
        ref_pr = {nid: 0.0 for nid in by_id}

    # 项目论文 PageRank（文献耦合无向图）
    try:
        cg = nx.Graph()
        cg.add_nodes_from(paper_ids)
        cg.add_weighted_edges_from(
            (a, b, w) for (a, b), w in coupling_pairs.items()
        )
        paper_pr = nx.pagerank(cg, alpha=0.85, weight="weight")
    except Exception as e:
        logger.warning("项目耦合 PageRank 失败: %s", e)
        paper_pr = {pid: 0.0 for pid in paper_ids}

    for nid, nd in by_id.items():
        pr = paper_pr.get(nid) if nd["role"] == "project_paper" else ref_pr.get(nid, 0.0)
        nd["pagerank"] = round(float(pr), 6)
        nd["degree"] = int(dg.degree(nid)) if nid in dg else 0

    # 社区发现：论文耦合图；参考文献归入其高频共引论文的社区
    try:
        comms = nx.community.greedy_modularity_communities(cg, weight="weight")
        paper_comm = {pid: idx for idx, members in enumerate(comms) for pid in members}
    except Exception:
        paper_comm = {pid: 0 for pid in paper_ids}

    for nd in nodes:
        if nd["role"] == "project_paper":
            nd["community"] = int(paper_comm.get(nd["id"], 0))
        else:
            citing = [pid for pid in ref_papers.get(nd["id"], set()) if pid in paper_comm]
            comm_votes = defaultdict(int)
            for pid in citing:
                comm_votes[paper_comm[pid]] += 1
            nd["community"] = int(max(comm_votes, key=comm_votes.get, default=0))

    # 核心参考文献排序：0.6×共被引 + 0.4×总被引
    core_score = {}
    for rid, meta in shared_refs.items():
        nd = by_id[rid]
        co = nd["co_cited_count"]
        core_score[rid] = 0.6 * co + 0.4 * (meta["cited_by_count"] or 0)
    core_ids = sorted(core_score, key=lambda rid: core_score[rid], reverse=True)[
        : int(cfg.get("core_reference_limit", 10))
    ]

    # 教科书式引用识别（与单篇 GNN 同口径）
    min_cites = int(cfg.get("textbook_min_citations", 500))
    min_co = int(cfg.get("textbook_min_co_cited", 2))
    min_age = int(cfg.get("textbook_min_age", 10))
    this_year = date.today().year
    textbook: list[dict] = []
    for rid, meta in shared_refs.items():
        nd = by_id[rid]
        age = (this_year - meta["publication_year"]) if meta.get("publication_year") else None
        ok = bool(
            age is not None and age >= min_age
            and meta["cited_by_count"] >= min_cites
            and nd["co_cited_count"] >= min_co
        )
        nd["is_textbook"] = ok
        if ok:
            textbook.append(nd)

    ranked_papers = sorted(
        (nd for nd in nodes if nd["role"] == "project_paper"),
        key=lambda nd: (nd["pagerank"], nd["cited_by_count"] or 0),
        reverse=True,
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "paper_count": len(papers_meta),
            "shared_reference_count": len(shared_refs),
            "coupling_edge_count": len(coupling_pairs),
            "community_count": len({nd["community"] for nd in nodes if nd["role"] == "project_paper"}),
            "core_references": [
                {
                    "title": by_id[rid]["title"],
                    "co_cited_count": by_id[rid]["co_cited_count"],
                    "cited_by_count": by_id[rid]["cited_by_count"],
                    "community": by_id[rid]["community"],
                    "is_textbook": by_id[rid].get("is_textbook", False),
                }
                for rid in core_ids
            ],
            "paper_ranking": [
                {
                    "id": nd["id"],
                    "title": nd["title"],
                    "pagerank": nd["pagerank"],
                    "degree": nd["degree"],
                    "community": nd["community"],
                }
                for nd in ranked_papers
            ],
            "top_coupling_pairs": sorted(
                [
                    {
                        "a": by_id[a]["title"],
                        "b": by_id[b]["title"],
                        "shared_references": w,
                    }
                    for (a, b), w in coupling_pairs.items()
                ],
                key=lambda x: x["shared_references"],
                reverse=True,
            )[:10],
            "textbook_citations": textbook,
        },
    }
