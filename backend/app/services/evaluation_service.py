"""核心评估管线：抓取 → 指标 → 内容 → 评分 → 决策 → 报告。

Pipeline 分两段：
- fetch_paper(): 抓取元数据（返回 Paper，状态 metadata_ok/partial）
- evaluate(): 同步定量+规则化内容评估；LLM 部分异步（tasks broker）补充
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.fields import apply_fetched
from app.models.paper import Paper
from app.services import distributions, metrics
from app.services.content import abstract_structure, figure_analysis, pdf_parser, relevance
from app.services.content.llm import get_llm_provider
from app.services.fetchers.base import FetcherOrchestrator, detect_missing
from app.services.review_detection import detect_review
from app.services.scoring import aggregate

logger = logging.getLogger(__name__)


async def fetch_paper(
    db: Session, project_id: str, doi: str, orchestrator: FetcherOrchestrator, force: bool = False
) -> Paper:
    """按 DOI 抓取并落库。DOI 已存在且非 force 时直接返回现有记录。"""
    existing = db.query(Paper).filter(Paper.doi == doi, Paper.project_id == project_id).first()
    if existing and not force:
        return existing
    if existing and force:
        paper = existing
        paper.status = "fetching"
        db.commit()
    else:
        paper = Paper(project_id=project_id, doi=doi, status="fetching")
        db.add(paper)
        db.commit()

    data, trace, missing = await orchestrator.fetch_by_doi(doi)
    return _apply_fetched(db, paper, data, missing, trace, mailto=orchestrator.mailto)


async def fetch_paper_by_title(
    db: Session, project_id: str, title: str, orchestrator: FetcherOrchestrator
) -> Paper:
    """按标题搜索抓取（OpenAlex 全文搜索兜底），返回 Paper。

    搜索结果带 DOI 时优先用 DOI 落库并做去重（避免标题命中已导入文献产生重复行）。
    """
    data, trace, missing = await orchestrator.fetch_by_title(title)
    doi = data.get("doi")

    # 标题搜索命中已有 DOI → 去重：同一项目内同 DOI 直接返回现有记录
    if doi:
        existing = db.query(Paper).filter(Paper.doi == doi, Paper.project_id == project_id).first()
        if existing:
            return existing

    paper = Paper(project_id=project_id, doi=doi, title=data.get("title"), status="fetching")
    db.add(paper)
    db.commit()
    return _apply_fetched(db, paper, data, missing, trace, mailto=orchestrator.mailto)


async def fetch_paper_by_pmid(
    db: Session, project_id: str, pmid: str, orchestrator: FetcherOrchestrator
) -> Paper:
    """PubMed 直接源导入：有 DOI 时叠加三源抓取，无 DOI 也能用 PubMed 元数据入库。"""
    from app.services.pubmed import pmid_to_meta

    pmid = (pmid or "").strip()
    if not pmid or not pmid.isdigit():
        raise ValueError(f"无效 PMID: {pmid or '(空)'}")

    meta = await pmid_to_meta(pmid, mailto=orchestrator.mailto)
    if not meta:
        raise ValueError(f"PMID {pmid} 查询失败")

    # 去重优先级：同项目同 PMID → 同项目同 DOI → 新建
    existing = (
        db.query(Paper)
        .filter(Paper.pmid == pmid, Paper.project_id == project_id)
        .first()
    )
    doi = meta.get("doi")
    if not existing and doi:
        existing = db.query(Paper).filter(Paper.doi == doi, Paper.project_id == project_id).first()
    if existing:
        if not existing.pmid:
            existing.pmid = pmid
            db.commit()
        return existing

    trace: list[dict] = []
    if doi:
        data, trace, missing = await orchestrator.fetch_by_doi(doi)
        # PubMed 官方元数据补充三源缺口（生物医学摘要/MeSH/PublicationType）
        for field in ("title", "journal", "abstract", "publication_year", "authors",
                      "issn", "volume", "issue", "pages"):
            if data.get(field) in (None, "", []) and meta.get(field) not in (None, "", []):
                data[field] = meta[field]
        if not data.get("keywords") and meta.get("keywords"):
            data["keywords"] = meta["keywords"]
        if meta.get("work_type") == "review" and data.get("work_type") != "review":
            data["work_type"] = "review"
        data["pmid"] = pmid
        trace.append({
            "source": "pubmed", "ok": True, "error": None,
            "fields": [k for k, v in meta.items() if v not in (None, "", [])],
        })
        missing = detect_missing(data)
    else:
        data = dict(meta)
        trace = [{
            "source": "pubmed", "ok": True, "error": None,
            "fields": [k for k, v in meta.items() if v not in (None, "", [])],
        }]
        missing = detect_missing(data)

    paper = Paper(project_id=project_id, doi=doi, pmid=pmid, status="fetching")
    db.add(paper)
    db.commit()
    return _apply_fetched(db, paper, data, missing, trace, mailto=orchestrator.mailto)


def _apply_fetched(
    db: Session, paper: Paper, data: dict, missing: list[str], trace: list[dict], mailto: str = ""
) -> Paper:
    """把抓取结果写入 Paper 并落库（fetch_paper / fetch_paper_by_title / fetch_paper_by_pmid 共用）。

    普通字段由 ``fields.apply_fetched`` 按注册表批量写入 —— 新增一个元数据字段
    只改 ``app/models/fields.py``，不必在这里逐行补 ``paper.x = data.get("x")``
    （漏改一处就是"抓到了却没落库"，且不报错）。
    """
    apply_fetched(paper, data)
    # 综述升格：OpenAlex 常把综述误标 article，综合 S2 publicationTypes + 标题启发式修正
    # （不走通用路径：需要 detect_review 综合两个源字段判断）
    paper.work_type = detect_review(
        data.get("work_type"), data.get("title"), data.get("s2_work_type"),
    )
    paper.missing_fields = missing
    paper.fetch_log = trace

    # 争议性检测（免费信号）：OpenAlex is_retracted / Crossref update-to 任一命中 → 撤稿
    retracted = _detect_retraction(data)
    if retracted:
        paper.is_retracted = True
        paper.fetch_log = trace + [{"source": "retraction", "ok": True, "error": None, "fields": ["is_retracted"], "retraction": retracted}]

    # 期刊 2yr_mean 缺失时从 /sources 端点补查（works 响应不带 summary_stats）
    if paper.journal_2yr_mean is None and paper.openalex_source_id:
        from app.services.journal_stats import get_2yr_mean

        paper.journal_2yr_mean = get_2yr_mean(paper.openalex_source_id, mailto=mailto)

    # 期刊学科百分位（同步计算并落库，避免评估期重复算；与 journal.compute 同口径：对数正态优先）
    if paper.journal_2yr_mean is not None:
        params = distributions.journal_lognormal_for(paper.subfield_id)
        if params:
            mu, sigma = params
            paper.journal_percentile = distributions.lognormal_percentile(mu, sigma, paper.journal_2yr_mean)
        if paper.journal_percentile is None:  # 无对数正态校准 → 经验分位兜底
            dist = distributions.journal_dist_for(paper.subfield_id)
            paper.journal_percentile = distributions.percentile_in(dist, paper.journal_2yr_mean)

    paper.status = "partial" if missing else "metadata_ok"
    db.commit()
    db.refresh(paper)
    return paper


async def evaluate(
    db: Session, paper_id: str, orchestrator: FetcherOrchestrator | None = None, use_llm: bool = False
) -> Paper:
    """执行完整评估。返回带 evaluation 的 Paper。

    use_llm=False：LLM 段强制规则档（快、省）；True：走真实 LLM
    provider（无 key 时工厂自动降级规则档）。v6 起 LLM 的 content_quality 作为
    第七分量参与 composite_score；无摘要且无全文时按缺数据中性 50。

    本函数只管状态机的开与闭：置 ``evaluating`` → 交给 ``_run_evaluation`` →
    任何异常都先写 ``failed`` 终态再向上抛。评估逻辑本体在 ``_run_evaluation``。
    """
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise ValueError(f"paper {paper_id} not found")
    paper.status = "evaluating"
    db.commit()
    try:
        return await _run_evaluation(db, paper, orchestrator, use_llm)
    except Exception as e:
        _mark_failed(db, paper_id, e)
        raise


def _mark_failed(db: Session, paper_id: str, exc: Exception) -> None:
    """把失败写成终态。

    本函数存在的唯一理由：``evaluate`` 开头已经 commit 了 ``status="evaluating"``，
    失败若不写回终态，这篇论文会永远停在"评估中"——前端分不清"卡死"与"进行中"，
    也没有任何字段说明为什么失败（2026-09-16 那次 51 篇全空，就是靠手工 force 才捞回来）。
    """
    try:
        db.rollback()  # 清掉失败事务里的半成品改动，否则 commit 会连带写脏数据
        paper = db.get(Paper, paper_id)
        if paper is None:
            return
        paper.status = "failed"
        paper.fetch_log = list(paper.fetch_log or []) + [
            {"source": "evaluate", "ok": False, "error": f"{type(exc).__name__}: {exc}", "fields": []}
        ]
        db.commit()
    except Exception:  # noqa: BLE001 — 落终态失败不得掩盖原始异常
        logger.exception("写入 failed 终态失败: %s", paper_id)


async def _run_evaluation(
    db: Session, paper: Paper, orchestrator: FetcherOrchestrator | None, use_llm: bool
) -> Paper:
    """评估主体：自愈 → 取证 → 内容判断 → 打分 → 决策 → 落库。

    不处理失败分支（那由 ``evaluate`` 负责）；抛出的异常会一路冒到那里写成终态。
    """
    _heal_journal_metric(paper, getattr(orchestrator, "mailto", "") or "")
    ctx = await _collect_evidence(paper, orchestrator)
    content = await _assess_content(paper, ctx, use_llm)
    scored = _score_paper(paper, ctx, content)
    decision = _decide_with_content(
        paper,
        scored["agg"],
        content["content_quality"],
        relevance=scored["relevance"],
        data_status=scored["data_status"],
        confidence=scored["confidence"],
    )
    # 三份中间产物键名互不重叠，合并后交给落库函数（避免 6 个以上参数）
    return _persist_evaluation(db, paper, {**ctx, **content, **scored}, decision)


def _heal_journal_metric(paper: Paper, mailto: str = "") -> None:
    """期刊 2yr_mean 自愈 + 百分位口径对齐。

    fetch 阶段的 /sources 补查若失败（典型是未带 API key 导致额度 429），它会一直是
    None，期刊维度（权重 0.18）整批退化成"缺数据"、档位被封顶。评估期补一次，
    让"重新评估"能把这类文献修回来，不必重新抓取。
    """
    if paper.journal_2yr_mean is None and paper.openalex_source_id:
        from app.services.journal_stats import get_2yr_mean

        paper.journal_2yr_mean = get_2yr_mean(paper.openalex_source_id, mailto=mailto)

    # 期刊百分位与最新对数正态口径对齐（旧论文可能是 fetch 阶段用旧算法落库，
    # 避免决策层 journal 门读到过时值——journal.compute 已用新口径）
    if paper.journal_2yr_mean is not None:
        params = distributions.journal_lognormal_for(paper.subfield_id)
        if params:
            mu, sigma = params
            paper.journal_percentile = distributions.lognormal_percentile(mu, sigma, paper.journal_2yr_mean)


async def _collect_evidence(paper: Paper, orchestrator: FetcherOrchestrator | None) -> dict:
    """抓取打分所需外部证据：近5年被引、参考引用年份、全文文本、项目上下文。

    三处外部查询失败都只告警不抛：证据缺失会让对应维度判"缺数据"，
    比整篇评估失败更可接受。
    """
    # 1. 近5年被引（OpenAlex 查询）
    cited_5yr = None
    if orchestrator and paper.openalex_work_id:
        try:
            cited_5yr = await _fetch_cited_5yr(orchestrator, paper.openalex_work_id)
            paper.cited_by_5yr = cited_5yr
        except Exception as e:
            logger.warning("近5年被引查询失败: %s", e)

    # 1b. 新文献（≤5年）参考引用年份（前沿度·引用新鲜度用，v5）
    ref_years = None
    if orchestrator and paper.openalex_work_id and paper.publication_year is not None:
        if date.today().year - paper.publication_year <= 5:
            try:
                ref_years = await _fetch_ref_years(orchestrator, paper.openalex_work_id)
            except Exception as e:
                logger.warning("参考引用年份查询失败: %s", e)

    # 2. 全文文本（可重复性/结构检测/相关性/方法过时检测用）
    fulltext = pdf_parser.extract_text(paper.fulltext_path) if paper.fulltext_path else None

    project = paper.project
    return {
        "cited_5yr": cited_5yr,
        "ref_years": ref_years,
        "fulltext": fulltext,
        "topic": project.research_topic if project else "",
        "keywords": project.keywords if project else [],
    }


async def _assess_content(paper: Paper, ctx: dict, use_llm: bool) -> dict:
    """LLM / 规则档内容评估，并把 content_quality 定成最终值。

    use_llm=False 强制规则档（后台降级用）；True 走真实 provider
    （无 key 时工厂自动降级规则档）。无摘要且无全文时按缺数据中性 50。
    """
    fulltext = ctx["fulltext"]
    topic = ctx["topic"]

    # 3. LLM 深度评估（提前到评分前：内容质量分以它为唯一来源，radar 与决策都要用。
    #    use_llm=False 强制规则档（后台降级用）；无 key 时 get_llm_provider 自动规则降级）
    if use_llm:
        llm_provider = get_llm_provider(get_settings())
    else:
        from app.services.content.llm.rule_based import RuleBasedProvider

        llm_provider = RuleBasedProvider()
    llm_assessment = await llm_provider.analyze(paper, {"fulltext": fulltext}, topic)
    citation_intent = llm_assessment.get("citation_value", {}).get("intent", "background")

    # 内容质量分：完全交由 LLM（研究设计/方法/结果一致性/可复现信号综合判断）。
    # 降级档（rule_based）也会出该字段；无摘要且无全文时缺数据=未知 → 中性 50。
    has_content_text = bool(paper.abstract or fulltext)
    if not has_content_text:
        llm_assessment["content_quality"] = {
            "score": 50.0,
            "detail": "摘要与全文均缺失，按缺数据中性 50 计（未知，不作质量判断）",
        }
    _cq = llm_assessment.get("content_quality") or {}
    llm_cq = _cq.get("score") if isinstance(_cq.get("score"), (int, float)) else None
    return {
        "llm_assessment": llm_assessment,
        "citation_intent": citation_intent,
        "has_content_text": has_content_text,
        "content_quality": float(llm_cq) if has_content_text and llm_cq is not None else 50.0,
    }


def _score_paper(paper: Paper, ctx: dict, content: dict) -> dict:
    """七路计量指标 + 内容结构/相关性 + 聚合 + 缺数据状态。

    纯计算：不碰数据库、不决定状态机。缺数据只在 data_status 里记账，
    由决策层三态门判 unknown（缺数据=未知，不是达标）。
    """
    # 上游证据/内容判断的局部别名，让以下算式与拆分前逐字一致
    fulltext = ctx["fulltext"]
    cited_5yr = ctx["cited_5yr"]
    ref_years = ctx["ref_years"]
    topic = ctx["topic"]
    keywords = ctx["keywords"]
    content_quality = content["content_quality"]
    has_content_text = content["has_content_text"]

    # 4. 六路计量指标（v6 再加内容质量 = 七因子）
    j = metrics.journal.compute(paper)
    c = metrics.citation.compute(paper, cited_by_5yr=cited_5yr)
    r = metrics.recency.compute(paper, cited_by_5yr=cited_5yr, ref_years=ref_years, fulltext=fulltext)
    a = metrics.author.compute(paper)
    rep = metrics.reproducibility.compute(paper, text=fulltext)
    components = {"journal": j["score"], "citation": c["score"], "timeliness": r["score"],
                  "author": a["score"], "reproducibility": rep["score"]}

    # 5. 内容评估（摘要缺失时用全文开头，解决源站无摘要 → 内容分=0 的痛点）
    struct_text = paper.abstract or (fulltext or "")[:5000]
    struct = abstract_structure.detect(struct_text)
    rel_text = paper.abstract or (fulltext or "")[:3000] or paper.title
    rel = relevance.score(topic, keywords, rel_text)

    # 6. 评分聚合 + 决策
    figure = figure_analysis.analyze(fulltext) if fulltext else None  # 仍落库供展示，v6 起内容质量由 LLM 进综合分
    confidence = aggregate.compute_confidence(paper, paper.missing_fields or [])
    # v4：相关性作为正式分量进综合分；v6：内容质量作为第七分量进综合分
    components["relevance"] = rel["score"] * 100
    components["content_quality"] = content_quality
    agg = aggregate.aggregate(
        components,
        radar_extra={"content_quality": content_quality, "relevance": rel["score"] * 100},
        confidence=confidence,
    )
    # data_status（v4/v6）：缺失标记，供决策三态门判 unknown（缺数据=未知，不是达标）
    data_status = {
        "journal": j.get("data_status", "ok"),
        "citation": c.get("data_status", "ok"),
        "timeliness": r.get("data_status", "ok"),
        "author": a.get("data_status", "ok"),
        "reproducibility": rep.get("data_status", "ok"),
        "content_quality": "missing" if not has_content_text else "ok",
    }
    return {
        "agg": agg,
        "data_status": data_status,
        "confidence": confidence,
        "relevance": rel["score"],  # 决策门用的 0-1 相关性
        "rel": rel,  # 落库展示用的完整相关性明细
        "rep": rep,
        "struct": struct,
        "figure": figure,
    }


def _persist_evaluation(db: Session, paper: Paper, outcome: dict, decision: dict) -> Paper:
    """落库评估结果与数据溯源，并把状态推进到终态 ``done``。

    指纹必须在所有 paper 字段写完之后算（含上游刚补的 journal_2yr_mean / cited_by_5yr）。
    """
    from app.models.evaluation import Evaluation
    from app.services import provenance

    agg = outcome["agg"]
    struct = outcome["struct"]

    ev = paper.evaluation
    if ev is None:
        ev = Evaluation(paper_id=paper.id)
        db.add(ev)
    paper.evaluation = ev  # 保证内存中关系一致（db.refresh 不刷新关系）
    ev.composite_score = agg["composite_score"]
    ev.weights = agg["weights"]
    ev.component_scores = agg["component_scores"]
    ev.radar_scores = agg["radar_scores"]
    ev.confidence = agg["confidence"]
    ev.abstract_structure = {
        "score": struct.score,
        "has_problem": struct.has_problem,
        "has_method": struct.has_method,
        "has_result": struct.has_result,
        "has_conclusion": struct.has_conclusion,
        "sample_size": struct.sample_size,
        "evidence_snippet": struct.evidence_snippet,
    }
    ev.relevance = outcome["rel"]
    ev.reproducibility = outcome["rep"]
    ev.figure_analysis = outcome["figure"]
    ev.llm_assessment = outcome["llm_assessment"]
    ev.citation_intent = outcome["citation_intent"]
    ev.decision = decision["decision"]
    ev.decision_reasons = decision["reasons"]
    ev.warnings = decision["warnings"]
    ev.tier = decision.get("tier")
    # 数据溯源：完整性只计算不落库是 09-16 那次误判的根源，这里一并存下。
    ev.data_status = outcome["data_status"]
    ev.data_fingerprint = provenance.compute_fingerprint(paper)

    paper.status = "done"
    db.commit()
    db.refresh(paper)
    return paper


async def _fetch_cited_5yr(orchestrator: FetcherOrchestrator, openalex_work_id: str) -> int | None:
    """近5年被引：OpenAlex filter=cites:{id},publication_year:{y-4}-{y}。"""
    from app.cache import cache

    this_year = date.today().year
    url = "https://api.openalex.org/works"
    cache_key = f"cited5yr:{openalex_work_id}"

    def _query() -> int:
        import httpx

        resp = httpx.get(
            url,
            params={
                "filter": f"cites:{openalex_work_id},publication_year:{this_year-4}-{this_year}",
                "per-page": 1,
                "mailto": orchestrator.mailto,
            },
            headers=get_settings().openalex_headers,  # 必须带 key，否则走无 key 额度易 429
            timeout=20,
        )
        resp.raise_for_status()
        return int(resp.json()["meta"]["count"])

    return cache.get_or_set(cache_key, _query, ttl=7 * 24 * 3600)


async def _fetch_ref_years(orchestrator: FetcherOrchestrator, openalex_work_id: str) -> list[int] | None:
    """参考引用发表年份：GET /works/{id}?select=referenced_works → 批量 openalex_id 查年份。

    复制 citation_graph._query_references 的批量模式（filter=openalex_id:{...} 每批 50），
    用同步 httpx（与 _fetch_cited_5yr 一致），缓存 reffresh:{id} 7 天。
    引用新鲜度分母只用实际返回且有年份的引用（不用 paper.reference_count——Crossref
    口径会系统性低估）。
    """
    from app.cache import cache

    url = "https://api.openalex.org/works"
    cache_key = f"reffresh:{openalex_work_id}"

    def _query() -> list[int]:
        import httpx

        r1 = httpx.get(
            f"{url}/{openalex_work_id}",
            params={"select": "referenced_works", "mailto": orchestrator.mailto},
            headers=get_settings().openalex_headers,  # 必须带 key，否则走无 key 额度易 429
            timeout=20,
        )
        r1.raise_for_status()
        ref_ids = r1.json().get("referenced_works", []) or []
        years: list[int] = []
        for i in range(0, len(ref_ids), 50):
            chunk = ref_ids[i : i + 50]
            r2 = httpx.get(
                url,
                params={
                    "filter": f"openalex_id:{'|'.join(chunk)}",
                    "per-page": 50,
                    "select": "id,publication_year",
                    "mailto": orchestrator.mailto,
                },
                headers=get_settings().openalex_headers,
                timeout=20,
            )
            r2.raise_for_status()
            for w in r2.json().get("results", []):
                if w.get("publication_year"):
                    years.append(int(w["publication_year"]))
        return sorted(years)

    return cache.get_or_set(cache_key, _query, ttl=7 * 24 * 3600)


def _decide_with_content(
    paper,
    agg: dict,
    content_quality: float,
    relevance: float | None = None,
    data_status: dict[str, str] | None = None,
    confidence: float | None = None,
) -> dict:
    from app.services.decision import decide

    # v3：decision 纳入综合分档位，传入与 aggregate 同源的 composite_score
    # v4：data_status 透传——缺数据=未知，关键门判 unknown、档位封顶
    # v6：confidence 透传——低置信度档位降一级
    result = decide(
        paper,
        {**agg["component_scores"], "content_quality": content_quality},
        composite_score=agg["composite_score"],
        relevance=relevance,  # v4：项目相关性，低于阈值档位封顶
        data_status=data_status,
        confidence=confidence,
    )
    return result


def _detect_retraction(data: dict) -> str | None:
    """争议性检测（免费信号）：返回撤稿说明或 None。

    - OpenAlex：works 的 is_retracted 布尔
    - Crossref：update-to 数组中含 retraction 类型的记录（更正/撤稿）
    """
    if data.get("is_retracted"):
        return "OpenAlex 标记该文献已撤稿"

    for upd in data.get("update_to") or []:
        if str(upd.get("type", "")).lower() in ("retraction", "retraction-of"):
            doi = upd.get("DOI") or upd.get("doi")
            return f"Crossref 存在撤稿记录{'（DOI ' + doi + '）' if doi else ''}"
    return None
