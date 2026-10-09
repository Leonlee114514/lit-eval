"""内容侧单测：摘要结构、图表识别、引用格式、规则档 LLM、综述检测、相关性（含语义预热）。"""

from __future__ import annotations

from tests.fakes import _MiniPaper, _force_coverage


def test_abstract_structure_english():
    from app.services.content.abstract_structure import detect

    abstract = (
        "We investigate the effect of crosslinking density on waterborne polyurethane "
        "coatings. We used FTIR, DSC and tensile tests (n=6). Results show significant "
        "improvement in mechanical properties (p<0.05). We conclude that multi-crosslinked "
        "bio-based networks offer superior durability."
    )
    r = detect(abstract)
    assert r.has_problem and r.has_method and r.has_result and r.has_conclusion
    assert r.sample_size == 6
    assert r.score > 90


def test_abstract_structure_chinese():
    from app.services.content.abstract_structure import detect

    abstract = (
        "本研究旨在探讨交联密度对水性聚氨酯性能的影响。采用FTIR、DSC和力学测试，"
        "样本量30。结果表明力学性能显著提升（p<0.05）。综上，本文表明多重交联体系具有应用前景。"
    )
    r = detect(abstract)
    assert r.has_problem and r.has_method and r.has_result and r.has_conclusion
    assert r.sample_size == 30


def test_abstract_structure_empty():
    from app.services.content.abstract_structure import detect

    assert detect(None).score == 0.0


def test_figure_analysis_data_vs_schematic():
    from app.services.content.figure_analysis import analyze

    text = (
        "Figure 1. Schematic illustration of the waterborne polyurethane network structure.\n"
        "Figure 2. FTIR spectra of crosslinked films showing characteristic absorption bands.\n"
        "Figure 3. Stress-strain curves at different crosslinking densities.\n"
        "Table 1. Mechanical properties summary.\n"
    )
    res = analyze(text)
    assert res["total_figures"] == 3
    assert res["tables"] == 1
    kinds = [f["kind"] for f in res["figures"]]
    assert kinds.count("schematic") == 1
    assert kinds.count("data") == 2
    assert res["score"] == 67  # 2/3 数据图占比


def test_citation_formats_three_styles():
    from app.services.citation_formats import format_all

    p = _MiniPaper(
        title="Deep learning",
        journal="Nature",
        publication_year=2015,
        volume="521",
        issue="7553",
        pages="436-444",
        doi="10.1038/nature14539",
        authors=[
            {"name": "LeCun, Yann"},
            {"name": "Bengio, Yoshua"},
            {"name": "Hinton, Geoffrey"},
        ],
    )
    r = format_all(p)
    assert r["apa"] and "LeCun, Y." in r["apa"]
    assert "&" in r["apa"]
    assert r["mla"] and "et al." in r["mla"]
    assert r["gbt7714"] and "[J]" in r["gbt7714"]
    assert "10.1038/nature14539" in r["apa"]


def test_citation_formats_single_author_no_volume():
    from app.services.citation_formats import format_all

    p = _MiniPaper(
        title="The FAIR Guiding Principles",
        journal="Scientific Data",
        publication_year=2016,
        authors=[{"name": "Wilkinson, Mark D."}],
    )
    r = format_all(p)
    assert r["apa"] and "Wilkinson, M." in r["apa"]
    assert r["mla"] and "Wilkinson, Mark" in r["mla"] or "Wilkinson, M." in r["mla"]
    assert r["gbt7714"]


def test_citation_format_none_for_empty():
    from app.services.citation_formats import format_citation

    assert format_citation(_MiniPaper(), "apa") is None


def test_figure_analysis_empty_and_no_text():
    from app.services.content.figure_analysis import analyze

    assert analyze(None) is None
    res = analyze("Some text without any figure captions.")
    assert res["total_figures"] == 0
    assert res["score"] is None


def test_figure_analysis_all_schematic_zero(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.figure_analysis import analyze

    text = (
        "Figure 1. Schematic representation of the proposed mechanism.\n"
        "Figure 2. Diagram of the experimental workflow.\n"
    )
    res = analyze(text)
    assert res["score"] == 0
    assert res["data_figures"] == 0
    assert res["schematic_figures"] == 2
    from app.services.content.relevance import score

    # 相关摘要：命中全部 4 个主题词（v5.1 同义词归一后 crosslinking↔crosslinked 视为同词）→ 1.0
    res = score("waterborne polyurethane coatings crosslinking", [], "crosslinked waterborne polyurethane coatings")
    assert res["method"] == "coverage"
    assert res["score"] == 1.0

    # 不相关摘要：0 命中 → 0.0（有区分度，可支撑阈值化降档）
    res2 = score("waterborne polyurethane crosslinking", [], "graphene based electronic sensor fabrication")
    assert res2["method"] == "coverage"
    assert res2["score"] == 0.0


def test_rule_based_llm_shape():
    import asyncio

    from app.services.content.llm.rule_based import RuleBasedProvider

    p = _MiniPaper(
        title="Crosslinked waterborne polyurethane",
        journal="Polymer Chemistry",
        publication_year=2016,
        abstract="We investigate the crosslinking of waterborne polyurethane. We used FTIR, DSC. Results show significant improvement (p<0.05). We conclude the network is promising.",
    )
    res = asyncio.run(RuleBasedProvider().analyze(p, {}, "polyurethane coatings"))
    assert res["source"] == "rule_based"
    assert set(res.keys()) == {"source", "research_design", "conclusion_reliability",
                               "content_quality", "citation_value", "controversy"}
    assert res["citation_value"]["intent"] in ("background", "method", "compare", "theory")
    assert 0 <= res["citation_value"]["confidence"] <= 100


def test_citation_intent_confidence_heuristic():
    import asyncio

    from app.services.content.llm.rule_based import RuleBasedProvider

    p = _MiniPaper(
        abstract="We developed a novel synthesis method. We propose a new framework for coating design based on polyurethane chemistry.",
    )
    intent, conf = asyncio.run(RuleBasedProvider().classify_citation_intent(p.abstract, "coatings"))
    assert intent == "method"  # 方法词占优 → method
    assert 0 < conf <= 100

    # 无摘要 → background + 低置信度
    intent2, conf2 = asyncio.run(RuleBasedProvider().classify_citation_intent("", "coatings"))
    assert intent2 == "background"
    assert conf2 < 50


def test_relevance_crosslingual_neutral(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 中文主题 + 空英文关键词 + 英文摘要 → 跨语中性 0.5，不再误判为 0
    res = score("水性聚氨酯涂层材料", [],
                "crosslinked waterborne polyurethane coatings for corrosion protection")
    assert res["method"] == "crosslingual"
    assert res["score"] == 0.5


def test_relevance_crosslingual_english_keywords_rescue(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 中文主题 + 英文关键词 → 跨语场景用英文关键词覆盖率（不稀释），不再中性 0.5
    res = score("水性聚氨酯涂层", ["waterborne polyurethane coatings"],
                "crosslinked waterborne polyurethane coatings")
    assert res["method"] == "coverage"
    assert res["score"] == 1.0


def test_relevance_english_same_script_still_zero(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 英文主题 + 不相关英文摘要（同脚本）→ 正常覆盖率 0.0，防跨语误中性
    res = score("waterborne polyurethane crosslinking", [],
                "graphene based electronic sensor fabrication")
    assert res["method"] == "coverage"
    assert res["score"] == 0.0


def test_relevance_synonym_waterborne_aqueous(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # Dieterich 案例：主题词 "waterborne"，正文用 "aqueous" → 同义词归一双向命中
    res = score("waterborne polyurethane", [], "aqueous polyurethane dispersions")
    assert res["method"] == "coverage"
    assert res["score"] == 1.0
    res2 = score("aqueous polyurethane", [], "waterborne polyurethane dispersions")
    assert res2["score"] == 1.0


def test_relevance_synonym_crosslink_verb_forms(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 动词形态归一：crosslink / crosslinked / crosslinking 视为同一词
    res = score("crosslink density", [], "crosslinking density of the cured films")
    assert res["method"] == "coverage"
    assert res["score"] == 1.0


def test_relevance_synonym_no_overmatch(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 归一表不收"相关但不同义"的词：waterproof 不能匹配 waterborne
    res = score("waterborne", [], "waterproof membrane fabrication")
    assert res["method"] == "coverage"
    assert res["score"] == 0.0


def test_relevance_fastembed_semantic_synonym():
    from app.services.content.relevance import score

    # fastembed 语义向量无需词表即识别同义：aqueous↔waterborne 高分（走默认路径）
    res = score("waterborne polyurethane", [], "aqueous polyurethane dispersions")
    assert res["method"] == "fastembed"
    assert res["score"] > 0.5


def test_relevance_fastembed_unrelated_low():
    from app.services.content.relevance import score

    # 无关主题 → 语义余弦低分
    res = score("waterborne polyurethane", [], "graphene sensor fabrication")
    assert res["method"] == "fastembed"
    assert res["score"] < 0.35


def test_relevance_no_abstract_title_fallback(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 无摘要但有标题 → 用标题覆盖率打分，method 带 _title 后缀，不再是中性 0.5
    res = score("waterborne polyurethane coatings crosslinking", [],
                None, "Crosslinked waterborne polyurethane coatings")
    assert res["method"] == "coverage_title"
    assert res["score"] > 0.5


def test_relevance_no_abstract_unrelated_title_low(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 无摘要且标题与主题无关 → 标题覆盖率低分（不再一律 0.5）
    res = score("waterborne polyurethane", [], None,
                "Graphene-based sensor fabrication using laser ablation")
    assert res["method"] == "coverage_title"
    assert res["score"] == 0.0


def test_relevance_no_abstract_no_title_neutral(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.content.relevance import score

    # 无摘要也无标题 → 中性 0.5（no_abstract）
    res = score("waterborne polyurethane", [], None)
    assert res["method"] == "no_abstract"
    assert res["score"] == 0.5


# ---- 综述类型检测（review_detection）----


def test_review_title_heuristic_upgrades_article():
    from app.services.review_detection import detect_review

    # OpenAlex 误标 article，但标题是 "A review" → 升格 review（本功能的核心场景）
    assert detect_review("article", "Waterborne polyurethanes: A review") == "review"


def test_review_s2_signal_upgrades_article():
    from app.services.review_detection import detect_review

    # 标题无 review 字样，但 S2 publicationTypes 标了 Review → 升格
    assert detect_review("article", "Advances in Waterborne Polyurethane Dispersions",
                         s2_signal="Review") == "review"


def test_review_never_demoted():
    from app.services.review_detection import detect_review

    # 已标 review 不因标题/信号缺失而降回 article
    assert detect_review("review", "Design and Synthesis of Waterborne Polyurethanes") == "review"


def test_review_plain_article_untouched():
    from app.services.review_detection import detect_review

    # 无任何 review 信号 → 原样保留 article
    assert detect_review("article", "Design and Synthesis of Waterborne Polyurethanes") == "article"
    assert detect_review(None, "Design and Synthesis of Waterborne Polyurethanes") is None


def test_review_meta_analysis_and_case_insensitive():
    from app.services.review_detection import detect_review

    assert detect_review("article", "Meta-analysis of X") == "review"
    assert detect_review("article", "REVIEW ARTICLES: Waterborne Polyurethane") == "review"


# ---- GNN 引用网络（P2 大项） ----


def _fake_fastembed_that_fails(monkeypatch):
    """把 fastembed.TextEmbedding 换成必定抛错的替身，并返回调用计数器。"""
    import pytest

    fastembed = pytest.importorskip("fastembed")
    calls = {"n": 0}

    class _Boom:
        def __init__(self, *a, **kw):
            calls["n"] += 1
            raise RuntimeError("model unavailable（模拟下载/权限失败）")

    monkeypatch.setattr(fastembed, "TextEmbedding", _Boom)
    return calls


def test_relevance_warm_up_caches_failure(monkeypatch):
    """预热失败要缓存成 False，且不再重复尝试 —— 否则每次调用都白等一次重试。"""
    from app.config import Settings
    from app.services.content import relevance as rel

    calls = _fake_fastembed_that_fails(monkeypatch)
    monkeypatch.setattr(rel, "_fastembed_model", None)
    monkeypatch.setattr(rel, "get_settings", lambda: Settings(use_fastembed=True))

    assert rel.warm_up() is False   # 首次尝试 → 失败
    assert rel.warm_up() is False   # 第二次直接读缓存的 False
    assert calls["n"] == 1, f"失败后不应重复尝试加载，实际 {calls['n']} 次"


def test_relevance_allow_load_false_never_loads_model(monkeypatch):
    """请求路径（allow_load=False）绝不能自己触发加载。

    首次加载可能是分钟级（下载 + fastembed 内部重试），放进请求里会撞爆前端 120s 超时。
    未就绪时应静默回退覆盖率，并且不把"未就绪"记成"失败"（预热仍可继续尝试）。
    """
    from app.config import Settings
    from app.services.content import relevance as rel

    calls = _fake_fastembed_that_fails(monkeypatch)
    monkeypatch.setattr(rel, "_fastembed_model", None)
    monkeypatch.setattr(rel, "get_settings", lambda: Settings(use_fastembed=True))

    res = rel.score(
        "waterborne polyurethane", [], "waterborne polyurethane coating study", None,
        allow_load=False,
    )
    assert res["method"] == "coverage"   # 优雅回退
    assert calls["n"] == 0, "请求路径不应触发模型加载"
    assert rel._fastembed_model is None, "未就绪不应被记成失败，预热仍可继续"


def test_relevance_allow_load_false_uses_model_when_already_ready(monkeypatch):
    """模型已就绪时，allow_load=False 仍然要用它 —— 只是不允许"顺带加载"。"""
    from app.config import Settings
    from app.services.content import relevance as rel

    class _Model:
        def embed(self, texts):
            import numpy as np

            return [np.array([1.0, 0.0]) for _ in texts]

    monkeypatch.setattr(rel, "_fastembed_model", _Model())
    monkeypatch.setattr(rel, "get_settings", lambda: Settings(use_fastembed=True))

    res = rel.score("topic", [], "abstract text", None, allow_load=False)
    assert res["method"] == "fastembed"
    assert res["score"] == 1.0


def test_search_service_scores_relevance_without_blocking(monkeypatch):
    """搜索候选重排必须传 allow_load=False（否则重启后第一次搜索会卡几分钟）。"""
    import asyncio

    from app.services import search_service
    from app.services.content import relevance

    seen: dict = {}

    def _spy(topic, keywords, abstract, title=None, **kw):
        seen.update(kw)
        return {"score": 0.5, "method": "coverage", "topic": topic, "basis": ""}

    class _OA:
        async def search_works(self, *a, **kw):
            return {"total": 1, "results": [{"doi": "10.x", "title": "t", "abstract": "a"}]}

    class _Orch:
        sources = {"openalex": _OA()}

    class _Project:
        research_topic = "waterborne polyurethane"
        keywords: list = []

    class _Q:
        def filter(self, *a, **kw):
            return self

        def all(self):
            return []

    class _DB:
        def get(self, *a):
            return _Project()

        def query(self, *a):
            return _Q()

    monkeypatch.setattr(relevance, "score", _spy)
    monkeypatch.setattr(search_service.relevance, "score", _spy)

    asyncio.run(
        search_service.search_works(_DB(), _Orch(), "q", per_page=1, project_id="p1")
    )
    assert seen.get("allow_load") is False, f"应传 allow_load=False，实际 {seen}"


def test_warmup_helper_skips_when_fastembed_disabled(monkeypatch):
    """没开 USE_FASTEMBED 就别预热（省一次无谓的模型加载）。"""
    import asyncio

    from app.config import Settings
    from app.main import _warm_semantic_models
    from app.services.content import relevance as rel

    called = {"n": 0}

    def _should_not_run():
        called["n"] += 1
        return True

    monkeypatch.setattr(rel, "warm_up", _should_not_run)
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(use_fastembed=False))

    asyncio.run(_warm_semantic_models())
    assert called["n"] == 0


# ---- OpenAlex API key 必须覆盖所有"直连"调用 ----
