"""单元测试：规则、结构检测、指标、决策。无需外网。"""
from __future__ import annotations

import math


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


def test_rules_weights_sum_to_one():
    from app.services.rules import get_rules

    rules = get_rules()
    assert abs(sum(rules["weights"].values()) - 1.0) < 1e-6
    assert set(rules["weights"].keys()) == {
        "journal", "citation", "timeliness", "author", "reproducibility", "relevance", "content_quality"
    }
    assert abs(rules["weights"]["content_quality"] - 0.10) < 1e-6
    assert abs(rules["confidence"]["low_threshold"] - 0.5) < 1e-6
    assert rules["decision"]["step2"]["citation_score_ge"] == 50
    assert rules["decision"]["relevance_low_threshold"] == 0.35


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


def test_journal_manual_quartile():
    from app.services.metrics.journal import compute

    p = _MiniPaper(jcr_quartile="Q1")
    res = compute(p)
    assert res["score"] == 90
    assert res["source"] == "manual_dual"


def test_journal_lognormal_automatic():
    from app.services.metrics.journal import compute
    from app.services import distributions

    # 无手填 → 对数正态自动档；分布中位数附近 → 约 50 中性分
    dist = distributions.journal_dist_for(None)
    p = _MiniPaper(journal_2yr_mean=dist[len(dist) // 2], subfield_id=None)
    res = compute(p)
    assert 30 <= res["score"] <= 70
    assert res["source"] == "lognormal"


def test_journal_manual_auto_take_max():
    from app.services.metrics.journal import compute

    # 手填 Q3=45 但期刊 2yr_mean 高 → 自动对数正态百分位更高 → 取自动值（v5.1 修正）
    p = _MiniPaper(jcr_quartile="Q3", journal_2yr_mean=25.0, subfield_id=None)
    res = compute(p)
    assert res["source"] == "manual_dual"
    assert res["auto_percentile"] is not None
    assert res["auto_percentile"] > 45
    assert res["score"] == res["auto_percentile"]
    assert "自动档更高" in res["basis"]


def test_journal_manual_higher_kept():
    from app.services.metrics.journal import compute

    # 手填 Q1=90 高于自动档 → 保留手填
    p = _MiniPaper(jcr_quartile="Q1", journal_2yr_mean=2.0, subfield_id=None)
    res = compute(p)
    assert res["score"] == 90
    assert res["basis"] == "w₁=max(JCR Q1=90)"


def test_h_index_global_fallback():
    from app.services import distributions

    # 分布文件只剩 global 键：任意 subfield 都应回退化学领域基准（不是旧默认 20）
    m_sub = distributions.h_index_mean_for("https://openalex.org/subfields/1707")
    m_global = distributions.h_index_mean_for(None)
    assert m_sub == m_global
    assert m_sub > 0


def test_citation_lognormal():
    from app.services.metrics.citation import compute

    # 无同年百分位 → 对数正态兜底；高被引高分、低被引低分（不饱和）
    p = _MiniPaper(cited_by_count=50000, subfield_id=None)
    res = compute(p)
    assert res["method"] == "lognormal"
    assert res["score"] > 90

    p2 = _MiniPaper(cited_by_count=1, subfield_id=None)
    res2 = compute(p2)
    assert res2["method"] == "lognormal"
    assert res2["score"] < 50


def test_citation_percentile_priority():
    from app.services.metrics.citation import compute

    # 同年被引百分位优先（OpenAlex 官方精确值），即使总被引很高也以百分位为准
    p = _MiniPaper(cited_by_count=50000, citation_percentile=42.0, subfield_id=None)
    res = compute(p)
    assert res["method"] == "percentile_year"
    assert res["score"] == 42.0


def test_citation_zero_new_paper():
    from app.services.metrics.citation import compute

    # 新论文被引为 0 且无百分位 → 0 分（非中性）
    p = _MiniPaper(cited_by_count=0, citation_percentile=None, subfield_id=None)
    res = compute(p)
    assert res["method"] == "zero"
    assert res["score"] == 0.0


def test_citation_percentile_fallback():
    from app.services.metrics.citation import compute

    # 无被引计数但有同年百分位 → 用百分位
    p = _MiniPaper(cited_by_count=0, citation_percentile=92.0, subfield_id=None)
    res = compute(p)
    assert res["method"] == "percentile_year"
    assert res["score"] == 92.0


def test_citation_review_discount():
    from app.services.metrics.citation import compute

    # 综述被引打 0.7 折（被引泡沫修正）；原创研究不打折
    p = _MiniPaper(citation_percentile=90.0, work_type="review")
    res = compute(p)
    assert res["score"] == 63.0  # 90 × 0.7
    assert "综述" in res["basis"]

    p2 = _MiniPaper(citation_percentile=90.0, work_type="article")
    assert compute(p2)["score"] == 90.0

    # 缺失路径不打折（无真实分数可折）
    p3 = _MiniPaper(cited_by_count=None, work_type="review")
    res3 = compute(p3)
    assert res3["method"] == "missing"
    assert res3["score"] == 50.0


def test_citation_self_citation_discount():
    from app.services.metrics.citation import compute

    # 自引率 50%（50/100）> 阈值 0.20 → 折算：over=(0.5-0.2)/(1-0.2)=0.375，
    # mult=1-0.375×0.5=0.8125 → 90×0.8125=73.125 → 73.1
    p = _MiniPaper(citation_percentile=90.0, self_citation_count=50, reference_count=100)
    res = compute(p)
    assert res["score"] == 73.1
    assert "自引率" in res["basis"]

    # 低于阈值 → 不打折
    p2 = _MiniPaper(citation_percentile=90.0, self_citation_count=10, reference_count=100)
    assert compute(p2)["score"] == 90.0

    # 无自引数据（Crossref 无 reference）→ 不打折
    p3 = _MiniPaper(citation_percentile=90.0)
    assert compute(p3)["score"] == 90.0


def test_citation_review_and_self_citation_stack():
    from app.services.metrics.citation import compute

    # 综述 + 高自引：90 × 0.7 × 0.8125 = 51.1875 → 51.2（两项独立泡沫修正叠加）
    p = _MiniPaper(
        citation_percentile=90.0, work_type="review",
        self_citation_count=50, reference_count=100,
    )
    res = compute(p)
    assert res["score"] == 51.2
    assert "综述" in res["basis"] and "自引率" in res["basis"]


# ---- v5 活跃度：老文献同龄近5年被引分位 / 新文献前沿度 ----

def _patch_band(monkeypatch, dist):
    """把 recent_citation_band_for 指到固定分布（校准文件未生成时用）。"""
    import app.services.distributions as d

    monkeypatch.setattr(d, "recent_citation_band_for", lambda age: dist)


def test_recency_old_paper_band_percentile(monkeypatch):
    from app.services.metrics.recency import compute

    # age 45 → 30+ 段；近5年被引 32 → ln(33)≈3.5 在段分布顶部 → 高分位（v4 时代是 0.2）
    band = sorted([0.0] * 80 + [math.log(2), math.log(3), math.log(6), math.log(33), math.log(33)])
    _patch_band(monkeypatch, band)
    p = _MiniPaper(publication_year=1981, cited_by_5yr=32, is_classic=False)
    res = compute(p)
    assert res["branch"] == "cohort"
    assert res["band"] == "30+"
    assert res["data_status"] == "ok"
    assert res["score"] >= 80


def test_recency_old_missing_cited5yr_neutral(monkeypatch):
    from app.services.metrics.recency import compute

    _patch_band(monkeypatch, [0.0, 0.1])
    p = _MiniPaper(publication_year=1981, cited_by_5yr=None, is_classic=False)
    res = compute(p)
    assert res["score"] == 50.0
    assert res["data_status"] == "missing"


def test_recency_old_no_band_dist_neutral(monkeypatch):
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    # 校准分布缺失（文件未生成）→ 中性 50，不崩
    monkeypatch.setattr(d, "recent_citation_band_for", lambda age: None)
    p = _MiniPaper(publication_year=1981, cited_by_5yr=32, is_classic=False)
    res = compute(p)
    assert res["score"] == 50.0
    assert res["data_status"] == "missing"


def test_recency_classic_floor_old_paper(monkeypatch):
    from app.services.metrics.recency import compute

    # 低分位老文献 + is_classic → 保底 75
    low = sorted([math.log(2)] * 10 + [math.log(33)] * 40)
    _patch_band(monkeypatch, low)
    p = _MiniPaper(publication_year=1981, cited_by_5yr=1, is_classic=True)
    res = compute(p)
    assert res["score"] == 75.0
    assert res["classic_floor"] is True

    # 高分位（近5年被引 100）→ 不触发保底
    high = sorted([math.log(2)] * 20 + [math.log(33)] * 80 + [math.log(101)])
    _patch_band(monkeypatch, high)
    p2 = _MiniPaper(publication_year=1981, cited_by_5yr=100, is_classic=True)
    res2 = compute(p2)
    assert res2["score"] > 75
    assert res2["classic_floor"] is False


def test_distributions_age_band_lookup():
    from app.services.distributions import age_band

    assert age_band(5) is None
    assert age_band(6) == "6-9"
    assert age_band(9) == "6-9"
    assert age_band(10) == "10-19"
    assert age_band(19) == "10-19"
    assert age_band(20) == "20-29"
    assert age_band(29) == "20-29"
    assert age_band(30) == "30+"
    assert age_band(100) == "30+"


def test_recency_frontier_age_freshness_scale(monkeypatch):
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    # 无热点词表/无引用年份 → 只有新鲜度信号，重归一化后即新鲜度本身
    monkeypatch.setattr(d, "hot_keywords", lambda: {})
    p0 = _MiniPaper(publication_year=2026, is_classic=False)  # age 0
    assert compute(p0)["score"] == 100.0
    p5 = _MiniPaper(publication_year=2021, is_classic=False)  # age 5 → 平滑交棒 55
    assert compute(p5)["score"] == 55.0


def test_recency_frontier_renormalization(monkeypatch):
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    monkeypatch.setattr(d, "hot_keywords", lambda: {})
    # age 2 → 新鲜度 82；ref_freshness=75（3/4 引用在 [y-2,y]）；hot 缺失 → (0.40×82+0.30×75)/0.70
    p = _MiniPaper(publication_year=2024, is_classic=False)
    res = compute(p, ref_years=[2022, 2023, 2024, 2019])
    exp = (0.40 * (55 + 45 * (1 - 2 / 5)) + 0.30 * 75) / 0.70
    assert abs(res["score"] - exp) < 0.5
    assert res["branch"] == "frontier"


def test_recency_frontier_ref_freshness_share(monkeypatch):
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    monkeypatch.setattr(d, "hot_keywords", lambda: {})
    # age 0 → 新鲜度 100；引用全在 [y-2,y] → 引用新鲜度 100 → 总分 100
    p = _MiniPaper(publication_year=2026, is_classic=False)
    res = compute(p, ref_years=[2024, 2025, 2026, 2025])
    assert res["score"] == 100.0


def test_recency_frontier_zero_refs_renormalize(monkeypatch):
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    monkeypatch.setattr(d, "hot_keywords", lambda: {})
    # 空引用年份 → 子信号缺失，不崩，只按新鲜度
    p = _MiniPaper(publication_year=2024, is_classic=False)
    res = compute(p, ref_years=[])
    assert abs(res["score"] - (55 + 45 * (1 - 2 / 5))) < 0.5
    assert res["branch"] == "frontier"


def test_recency_hot_topic_tfidf(monkeypatch):
    _force_coverage(monkeypatch)
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    monkeypatch.setattr(
        d,
        "hot_keywords",
        lambda: {
            "terms": [
                {"term": "photocatalysis", "idf": 4.0},
                {"term": "metal organic framework", "idf": 3.5},
            ],
            "top_text": "photocatalysis metal organic framework",
        },
    )
    # 命中热点词 → 前沿度高于纯新鲜度
    hit = _MiniPaper(
        publication_year=2024, title="Photocatalysis for water splitting",
        abstract="novel photocatalysis system", is_classic=False,
    )
    assert compute(hit)["score"] > 70
    # 无关文本 → 热点信号=0（可用非缺失）→ 前沿度 = (0.40×新鲜度)/0.70，低于纯新鲜度
    miss = _MiniPaper(publication_year=2024, title="graphene sensor fabrication", is_classic=False)
    res2 = compute(miss)
    exp_miss = 0.40 * (55 + 45 * (1 - 2 / 5)) / 0.70
    assert abs(res2["score"] - exp_miss) < 0.5


def test_recency_method_penalty(monkeypatch):
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    monkeypatch.setattr(d, "hot_keywords", lambda: {})
    p = _MiniPaper(publication_year=2024, is_classic=False)
    # 全文含 B3LYP + 6-31G(d)（方法过时规则）→ 惩罚拉低；age2 新鲜度 82 − 30 = 52
    text = "We optimized the transition metal complex with B3LYP/6-31G(d). " + "B3LYP " * 10
    res = compute(p, fulltext=text)
    assert res["score"] == 52.0
    # 无全文 → 不惩罚
    res2 = compute(p)
    assert abs(res2["score"] - (55 + 45 * (1 - 2 / 5))) < 0.5


def test_recency_boundary_age5_vs_age6(monkeypatch):
    from app.services.metrics.recency import compute
    import app.services.distributions as d

    monkeypatch.setattr(d, "hot_keywords", lambda: {})
    _patch_band(monkeypatch, [0.0, math.log(2), math.log(33)])
    p5 = _MiniPaper(publication_year=2021, is_classic=False)  # age 5 → 前沿度
    assert compute(p5)["branch"] == "frontier"
    p6 = _MiniPaper(publication_year=2020, cited_by_5yr=2, is_classic=False)  # age 6 → 同龄段
    res6 = compute(p6)
    assert res6["branch"] == "cohort"
    assert res6["band"] == "6-9"


def test_reproducibility_regex():
    from app.services.metrics.reproducibility import compute

    p = _MiniPaper(has_data_availability_stmt=False)
    res = compute(p, text="Data availability: all code at github.com/acme/foo; supplement S1.")
    assert res["flags"]["data_availability"] and res["flags"]["code_repo"]
    assert res["flags"]["supplement"]
    assert res["score"] == 70  # 数据0.25+代码0.25+补充0.20


def test_reproducibility_penalties():
    from app.services.metrics.reproducibility import compute

    # 撤稿 → 归零
    p = _MiniPaper(is_retracted=True)
    assert compute(p)["score"] == 0.0

    # 商业资助且无 COI → -15
    p2 = _MiniPaper(has_data_availability_stmt=False)
    res = compute(p2, text="This study was funded by an industry sponsor. We used n=30 samples.")
    assert res["score"] <= 35 and res["penalties"]  # 商业 -15（n=30 无 n=1 惩罚）
    assert "商业资助" in res["basis"]


def test_reproducibility_neutral_when_no_data():
    from app.services.metrics.reproducibility import compute

    # 无全文 且 三个 has_* 全 None（从未判断过）→ 中性 50，而非 0 惩罚
    p = _MiniPaper()
    res = compute(p)
    assert res["score"] == 50.0
    assert "中性" in res["basis"]


def test_reproducibility_industry_no_false_positive():
    from app.services.metrics.reproducibility import compute

    # 收紧后：正文提到 "industry"（非资助语境）不再误判为商业资助
    p = _MiniPaper()
    res = compute(p, text="This paper compares industry standards with laboratory practice.")
    assert res["penalties"] == []
    assert not res["flags"]["coi_statement"]


def test_reproducibility_era_aware_old_paper():
    from app.services.metrics.reproducibility import compute

    # 老文献（1981）缺现代可重复性声明 → 时代错位保护，中性 50 而非 0
    p = _MiniPaper(publication_year=1981)
    res = compute(p, text="Aqueous polyurethane dispersions were prepared by phase inversion.")
    assert res["score"] == 50.0
    assert res.get("era_neutral") is True
    assert "非当时学术惯例" in res["basis"]


def test_reproducibility_era_not_applied_to_modern():
    from app.services.metrics.reproducibility import compute

    # 现代论文（2024）缺声明 → 仍如实 0（时代保护只给老文献）
    p = _MiniPaper(publication_year=2024)
    res = compute(p, text="We developed a novel waterborne polyurethane coating.")
    assert res["score"] == 0.0
    assert not res.get("era_neutral")


def test_reproducibility_era_old_with_evidence_still_scores():
    from app.services.metrics.reproducibility import compute

    # 老文献有时代内证据（补充材料）→ 仍计分，不受时代保护覆盖
    p = _MiniPaper(publication_year=1985)
    res = compute(p, text="Supplementary material is available on request.")
    assert res["flags"]["supplement"]
    assert res["score"] == 20  # 0.20×100
    assert not res.get("era_neutral")


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


def test_decision_nature_like():
    from app.services.decision import decide

    p = _MiniPaper(
        publication_year=2015, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=50000, cited_by_5yr=1000, work_type="review",
        journal_percentile=97.0, fetch_log=[],
    )
    # v3：decision 纳入综合分档位；Q1+IF40+百分位97+被引双过 + 综合分75(recommended) → deep_read
    res = decide(p, {"journal": 95, "citation": 98, "content_quality": 80}, composite_score=75.0)
    assert res["decision"] == "deep_read"
    assert res["tier"] in ("high_priority", "recommended")
    assert any("被引泡沫" in w for w in res["warnings"])


def test_decision_tier_consistent():
    from app.services.decision import decide

    # v3 调和核心：期刊被引双过但综合分仅 45(conditional) → 不再判 deep_read（消除矛盾）
    p = _MiniPaper(
        publication_year=2015, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=50000, cited_by_5yr=1000, work_type="review",
        journal_percentile=97.0, fetch_log=[],
    )
    res = decide(p, {"journal": 95, "citation": 98, "content_quality": 80}, composite_score=45.0)
    assert res["tier"] == "conditional"
    assert res["decision"] != "deep_read"
    assert res["decision"] == "background_only"


def test_self_citation_warning():
    from app.services.decision import decide

    # 参考自引率 40%（40/100）> 阈值 0.20 → "自引偏高"告警触发
    p = _MiniPaper(
        publication_year=2015, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=100, cited_by_5yr=10, work_type="article",
        journal_percentile=90.0, self_citation_count=40, reference_count=100,
    )
    res = decide(p, {"journal": 90, "citation": 80, "content_quality": 70}, composite_score=70.0)
    assert any("自引" in w for w in res["warnings"])

    # 自引率低（Phase 真实值 4/53）→ 不告警
    p2 = _MiniPaper(
        publication_year=2016, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=634, cited_by_5yr=100, work_type="article",
        journal_percentile=90.0, self_citation_count=4, reference_count=53,
    )
    res2 = decide(p2, {"journal": 90, "citation": 80, "content_quality": 70}, composite_score=70.0)
    assert not any("自引" in w for w in res2["warnings"])


def test_decision_retracted_forces_reject():
    from app.services.decision import decide

    # 顶刊高被引但被撤稿 → 直接 reject，无视其他指标
    p = _MiniPaper(
        publication_year=2015, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=50000, cited_by_5yr=1000, work_type="article",
        journal_percentile=97.0, is_retracted=True,
    )
    res = decide(p, {"journal": 95, "citation": 98, "content_quality": 80}, composite_score=90.0)
    assert res["decision"] == "reject"
    assert res["tier"] == "not_recommended"
    assert any("撤稿" in w for w in res["warnings"])


def test_detect_retraction_sources():
    from app.services.evaluation_service import _detect_retraction

    # OpenAlex is_retracted
    assert "撤稿" in _detect_retraction({"is_retracted": True})
    assert _detect_retraction({"is_retracted": False}) is None
    # Crossref update-to retraction
    upd = [{"type": "retraction", "DOI": "10.1000/retract"}]
    r = _detect_retraction({"update_to": upd})
    assert r and "10.1000/retract" in r
    # 无信号
    assert _detect_retraction({}) is None


def test_decision_reject_new_unknown():
    from app.services.decision import decide

    p = _MiniPaper(
        publication_year=2026, jcr_quartile=None, impact_factor=None,
        cited_by_count=0, cited_by_5yr=0, work_type="article",
        journal_percentile=None, fetch_log=[],
    )
    # v4：被引分 10（低于化学领域中位 50）→ 期刊被引双门均不过 + 综合分<35 → reject
    res = decide(p, {"journal": 50, "citation": 10, "content_quality": 40})
    assert res["decision"] == "reject"


def test_decision_relevance_cap():
    from app.services.decision import decide

    # 顶刊高被引（综合分 85→high_priority）但相关性低 → 档位封顶 conditional，不再 deep_read
    p = _MiniPaper(
        publication_year=2015, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=50000, cited_by_5yr=1000, work_type="article",
        journal_percentile=97.0, fetch_log=[],
    )
    res = decide(p, {"journal": 95, "citation": 98, "content_quality": 80},
                 composite_score=85.0, relevance=0.10)
    assert res["tier"] == "conditional"
    assert res["decision"] == "background_only"
    assert any("相关性" in r for r in res["reasons"])

    # 相关性中位以上 → 不封顶，正常 deep_read
    res2 = decide(p, {"journal": 95, "citation": 98, "content_quality": 80},
                  composite_score=85.0, relevance=0.60)
    assert res2["tier"] == "high_priority"
    assert res2["decision"] == "deep_read"


def test_decision_low_confidence_downgrades_tier():
    from app.services.decision import decide

    p = _MiniPaper(
        publication_year=2015, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=50000, cited_by_5yr=1000, work_type="article",
        journal_percentile=97.0, fetch_log=[],
    )
    comps = {"journal": 95, "citation": 98, "content_quality": 80}

    # 高置信度：high_priority 保持
    res = decide(p, comps, composite_score=90.0, confidence=0.9)
    assert res["tier"] == "high_priority"
    assert res["decision"] == "deep_read"

    # 低置信度：high_priority → recommended（仍 deep_read）
    res2 = decide(p, comps, composite_score=90.0, confidence=0.3)
    assert res2["tier"] == "recommended"
    assert res2["decision"] == "deep_read"
    assert any("置信度" in w for w in res2["warnings"])
    assert any("降一级" in r for r in res2["reasons"])

    # 低置信度：recommended → conditional（不再 deep_read）
    res3 = decide(p, comps, composite_score=70.0, confidence=0.3)
    assert res3["tier"] == "conditional"
    assert res3["decision"] == "background_only"

    # not_recommended 不再降
    res4 = decide(p, {"journal": 20, "citation": 10, "content_quality": 10},
                  composite_score=20.0, confidence=0.1)
    assert res4["tier"] == "not_recommended"


def test_tier_mapping():
    from app.services.decision import compute_tier

    assert compute_tier(90) == "high_priority"
    assert compute_tier(70) == "recommended"
    assert compute_tier(45) == "conditional"
    assert compute_tier(20) == "not_recommended"


def test_author_first_corresponding_blend():
    from app.services.metrics.author import compute
    from app.services import distributions

    # 通讯作者与一作不同 → 0.7×通讯 + 0.3×一作
    # field_median 读实时校准值（2026-08-02 起为化学领域 h-index 中位数，不再是硬编码 20）
    m = distributions.h_index_mean_for(None)
    p = _MiniPaper(
        subfield_id=None,
        authors=[
            {"name": "A", "h_index": 40, "is_corresponding": False},
            {"name": "B", "h_index": 10, "is_corresponding": True},
        ],
    )
    res = compute(p)
    # v4 去饱和：通讯 h=10 → 100×10/(10+m)；一作 h=40 → 100×40/(40+m)；混合 0.7+0.3
    exp_corr = 100.0 * 10.0 / (10.0 + m)
    exp_first = 100.0 * 40.0 / (40.0 + m)
    exp = 0.7 * exp_corr + 0.3 * exp_first
    assert abs(res["score"] - exp) < 1


def test_journal_dual_track_max():
    from app.services.metrics.journal import compute

    # JCR Q4(20) 与 中科院1区(100) 都有 → 取高 100
    p = _MiniPaper(jcr_quartile="Q4", cas_zone=1)
    res = compute(p)
    assert res["score"] == 100.0
    assert res["source"] == "manual_dual"


# ---- v4：相关性入综合分 / 缺数据=未知 / h-index 去饱和 / 跨语相关性 ----

def test_aggregate_composite_includes_relevance_and_content_quality():
    from app.services.scoring.aggregate import aggregate

    all_high = {k: 100 for k in
                ("journal", "citation", "timeliness", "author", "reproducibility", "relevance")}
    agg1 = aggregate(all_high, radar_extra={"content_quality": 100}, confidence=1.0)
    assert abs(agg1["composite_score"] - 100.0) < 0.1

    # v6：relevance=0、其余全 100 → 综合分 = 1 - relevance 权重(0.18) = 82
    no_rel = dict(all_high)
    no_rel["relevance"] = 0
    agg2 = aggregate(no_rel, radar_extra={"content_quality": 100}, confidence=1.0)
    assert abs(agg2["composite_score"] - 82.0) < 0.1
    # radar 仍恰好 5 键
    assert set(agg2["radar_scores"].keys()) == {
        "journal_level", "citation_impact", "content_quality", "timeliness", "relevance"
    }
    assert abs(agg2["radar_scores"]["relevance"] - 0.0) < 1e-6

    # v6：content_quality 是第七分量（权重 0.10）
    cq0 = aggregate(all_high, radar_extra={"content_quality": 0}, confidence=1.0)
    assert abs(agg1["composite_score"] - cq0["composite_score"] - 10.0) < 0.1


def test_aggregate_content_quality_missing_neutral():
    from app.services.scoring.aggregate import aggregate

    # 旧调用没有 content_quality（也未从 radar_extra 传）→ 按缺数据中性 50
    six = {k: 100 for k in
           ("journal", "citation", "timeliness", "author", "reproducibility", "relevance")}
    agg = aggregate(six, radar_extra={}, confidence=1.0)
    assert agg["component_scores"]["content_quality"] == 50.0
    assert abs(agg["composite_score"] - 95.0) < 0.1


def test_compute_confidence_fulltext_substitutes_abstract():
    from app.services.scoring.aggregate import compute_confidence

    p = _MiniPaper(title="t", publication_year=2020, journal="j", abstract=None,
                   fulltext_path="C:/x.pdf", cited_by_count=10, authors=["a"],
                   journal_2yr_mean=2.5)
    assert abs(compute_confidence(p, []) - 1.0) < 1e-9

    p2 = _MiniPaper(title="t", publication_year=None, journal=None, abstract=None,
                    fulltext_path=None, cited_by_count=None, authors=[],
                    journal_2yr_mean=None)
    assert abs(compute_confidence(p2, []) - 1 / 7) < 1e-9


def test_citation_data_status_missing_vs_zero():
    from app.services.metrics.citation import compute

    # 无被引计数（None）→ 缺失（中性 50），不是"被引=0"
    p = _MiniPaper(cited_by_count=None, citation_percentile=None, subfield_id=None)
    res = compute(p)
    assert res["method"] == "missing"
    assert res["data_status"] == "missing"
    assert res["score"] == 50.0

    # 被引=0（真实数据）→ 0 分、data_status=ok
    p2 = _MiniPaper(cited_by_count=0, citation_percentile=None, subfield_id=None)
    res2 = compute(p2)
    assert res2["method"] == "zero"
    assert res2["data_status"] == "ok"
    assert res2["score"] == 0.0


def test_journal_data_status_missing():
    from app.services.metrics.journal import compute

    # 无期刊任何数据 → missing
    p = _MiniPaper(journal_percentile=None)
    res = compute(p)
    assert res["source"] == "missing"
    assert res["data_status"] == "missing"
    assert res["score"] == 50.0

    # fetch 期经验分位兜底落库了 journal_percentile → 门有真实信号，不误报缺失
    p2 = _MiniPaper(journal_percentile=80.0)
    res2 = compute(p2)
    assert res2["source"] == "missing"
    assert res2["data_status"] == "ok"


def test_reproducibility_data_status_missing():
    from app.services.metrics.reproducibility import compute

    p = _MiniPaper()
    res = compute(p)
    assert res["data_status"] == "missing"
    assert res["score"] == 50.0


def test_author_data_status_missing():
    from app.services.metrics.author import compute

    p = _MiniPaper(authors=[])
    res = compute(p)
    assert res["data_status"] == "missing"
    assert res["score"] == 50.0


def test_decision_citation_missing_caps_tier():
    from app.services.decision import decide

    # 期刊双过 + 综合分 85(high_priority) + 高相关性，但被引数据缺失 → 封顶 conditional
    p = _MiniPaper(
        publication_year=2015, jcr_quartile="Q1", impact_factor=40.0,
        cited_by_count=None, work_type="article", journal_percentile=97.0,
    )
    res = decide(p, {"journal": 95, "citation": 50, "content_quality": 80},
                 composite_score=85.0, relevance=0.8,
                 data_status={"citation": "missing"})
    assert res["tier"] == "conditional"
    assert res["decision"] == "background_only"
    assert any("缺失" in w for w in res["warnings"])


def test_decision_journal_missing_caps_tier():
    from app.services.decision import decide

    # 被引双过 + 综合分 85 + 高相关性，但期刊数据缺失 → 封顶 conditional
    p = _MiniPaper(
        publication_year=2015, jcr_quartile=None, cited_by_count=50000,
        work_type="article", journal_percentile=None,
    )
    res = decide(p, {"journal": 50, "citation": 98, "content_quality": 80},
                 composite_score=85.0, relevance=0.8,
                 data_status={"journal": "missing"})
    assert res["tier"] == "conditional"
    assert res["decision"] == "background_only"
    assert any("缺失" in w for w in res["warnings"])


def test_decision_all_missing_background_only():
    from app.services.decision import decide

    # 综合分 50(recommended) + 期刊/被引全缺失 → 封顶 conditional，且非 reject
    p = _MiniPaper(publication_year=None, jcr_quartile=None, cited_by_count=None,
                   journal_percentile=None, work_type="article")
    res = decide(p, {"journal": 50, "citation": 50, "content_quality": 50},
                 composite_score=50.0,
                 data_status={"journal": "missing", "citation": "missing"})
    assert res["tier"] == "conditional"
    assert res["decision"] == "background_only"


def test_decision_missing_unknown_not_reject():
    from app.services.decision import decide

    # 综合分 20(not_recommended) + 双门缺失 → unknown≠fail：不 reject，判 background_only
    p = _MiniPaper(publication_year=None, jcr_quartile=None, cited_by_count=None,
                   journal_percentile=None, work_type="article")
    res = decide(p, {"journal": 50, "citation": 50, "content_quality": 20},
                 composite_score=20.0,
                 data_status={"journal": "missing", "citation": "missing"})
    assert res["tier"] == "not_recommended"
    assert res["decision"] == "background_only"


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

def test_citation_network_build_network():
    from app.services.citation_network import _build_network

    base = {
        "root": {"id": "root", "title": "Root paper", "cited_by_count": 50, "publication_year": 2020},
        "references": [
            {"id": "R1", "title": "Foundational old paper", "cited_by_count": 900, "publication_year": 1995},
            {"id": "R2", "title": "Recent method", "cited_by_count": 30, "publication_year": 2022},
        ],
        "citing": [
            {"id": "C1", "title": "Citing one", "cited_by_count": 10, "publication_year": 2023},
            {"id": "C2", "title": "Citing two", "cited_by_count": 5, "publication_year": 2024},
        ],
    }
    cfg = {"textbook_min_citations": 500, "textbook_min_co_cited": 2, "textbook_min_age": 10}
    net = _build_network(base, root_ref_ids={"R1", "R2", "R9"},
                         citing_ref_sets={"C1": {"R1", "R2"}, "C2": {"R1"}}, cfg=cfg)

    assert net["stats"]["node_count"] == 5
    assert net["stats"]["edge_count"] == 7  # C1->root, C2->root, root->R1, root->R2, C1->R1, C1->R2, C2->R1
    by_id = {n["id"]: n for n in net["nodes"]}
    assert by_id["R1"]["co_cited_count"] == 2
    assert by_id["R1"]["is_textbook"] is True  # 1995、900 被引、2 篇施引文献同时引用
    assert by_id["R2"]["is_textbook"] is False
    assert by_id["C1"]["bibliographic_coupling"] == 2
    assert by_id["C2"]["bibliographic_coupling"] == 1
    assert set(by_id["root"]) >= {"pagerank", "community", "role"}
    assert net["root"]["id"] == "root"
    # 所有边都落在节点集合内
    ids = set(by_id)
    assert all(e["source"] in ids and e["target"] in ids for e in net["edges"])
    # 教科书式引用进入统计
    assert net["stats"]["textbook_citations"][0]["id"] == "R1"


# ---- PubMed 直接源（P2 大项） ----

def test_parse_pubmed_xml_fields():
    from app.services.pubmed import _parse_pubmed_xml

    xml = """<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation>
<PMID>123456</PMID>
<Article>
<Journal><ISSN>1234-5678</ISSN><JournalIssue><Volume>10</Volume><Issue>2</Issue>
<PubDate><Year>2021</Year></PubDate></JournalIssue><Title>Test Journal</Title></Journal>
<ArticleTitle>Test article title.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Some background.</AbstractText></Abstract>
<AuthorList><Author><LastName>Smith</LastName><ForeName>John</ForeName></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Review</PublicationType></PublicationTypeList>
</Article><MeshHeadingList><MeshHeading><DescriptorName>Test Topic</DescriptorName></MeshHeading></MeshHeadingList>
</MedlineCitation><PubmedData><ArticleIdList>
<ArticleId IdType="doi">10.1000/test</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

    meta = _parse_pubmed_xml(xml, "123456")
    assert meta is not None
    assert meta["pmid"] == "123456"
    assert meta["doi"] == "10.1000/test"
    assert meta["title"] == "Test article title."
    assert meta["journal"] == "Test Journal"
    assert meta["publication_year"] == 2021
    assert meta["volume"] == "10" and meta["issue"] == "2"
    assert meta["authors"][0]["name"] == "John Smith"
    assert meta["keywords"] == ["Test Topic"]
    assert meta["work_type"] == "review"
    assert "BACKGROUND" in meta["abstract"]


def test_parse_pubmed_xml_no_doi():
    from app.services.pubmed import _parse_pubmed_xml

    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>
<Journal><JournalIssue><PubDate><MedlineDate>2020 Jan-Feb</MedlineDate></PubDate></JournalIssue><Title>J</Title></Journal>
<ArticleTitle>T</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    meta = _parse_pubmed_xml(xml, "9")
    assert meta is not None
    assert meta["doi"] is None
    assert meta["publication_year"] == 2020


# ---- 项目级 GNN 引用网络 ----

def test_project_network_build():
    from app.services.project_network import _build_project_network

    papers = [
        {"id": "P1", "openalex_id": "W1", "title": "Paper one", "cited_by_count": 10, "publication_year": 2020},
        {"id": "P2", "openalex_id": "W2", "title": "Paper two", "cited_by_count": 20, "publication_year": 2021},
        {"id": "P3", "openalex_id": "W3", "title": "Paper three", "cited_by_count": 30, "publication_year": 2022},
    ]
    ref_meta = {
        "R1": {"id": "R1", "title": "Old core reference", "cited_by_count": 900, "publication_year": 1990},
        "R2": {"id": "R2", "title": "Shared method", "cited_by_count": 40, "publication_year": 2021},
    }
    paper_refs = {
        "P1": {"R1", "R2"}, "P2": {"R1", "R2"}, "P3": {"R1"},
    }
    ref_papers = {"R1": {"P1", "P2", "P3"}, "R2": {"P1", "P2"}}
    cfg = {
        "min_co_cited": 2, "max_references": 60, "coupling_min_shared": 1,
        "core_reference_limit": 10,
        "textbook_min_citations": 500, "textbook_min_co_cited": 2, "textbook_min_age": 10,
    }
    net = _build_project_network(papers, ref_meta, paper_refs, ref_papers, cfg)

    assert net["stats"]["paper_count"] == 3
    assert net["stats"]["shared_reference_count"] == 2
    assert net["stats"]["coupling_edge_count"] == 3  # P1-P2, P1-P3, P2-P3
    by_id = {n["id"]: n for n in net["nodes"]}
    assert by_id["R1"]["co_cited_count"] == 3
    assert by_id["R1"]["is_textbook"] is True
    assert by_id["R2"]["is_textbook"] is False
    assert by_id["P1"]["role"] == "project_paper"
    assert by_id["P1"]["pagerank"] > 0
    assert net["stats"]["textbook_citations"][0]["id"] == "R1"
    assert net["stats"]["top_coupling_pairs"][0]["shared_references"] == 2
    assert set(net["stats"]["core_references"][0]) >= {"title", "co_cited_count", "cited_by_count"}


# ---- 检索式构造（fetchers/query_builder.py，纯函数、离线）----


def _group_terms(variant: str) -> list[str]:
    """把渲染后的检索式拆回词项列表（测试辅助：仅处理本用例的形态）。"""
    terms: list[str] = []
    for grp in variant.split(" AND "):
        grp = grp.strip()
        if grp.startswith("(") and grp.endswith(")"):
            terms.extend(t.strip() for t in grp[1:-1].split(" OR "))
        elif grp:
            terms.append(grp)
    return terms


def test_query_builder_invert_synonyms():
    from app.services.fetchers.query_builder import invert_synonym_map

    inverted = invert_synonym_map({"aqueous": "waterborne", "waterbased": "waterborne"})
    # 规范词自身也进组，且排序确定
    assert inverted == {"waterborne": ["aqueous", "waterbased", "waterborne"]}
    assert invert_synonym_map(None) == {}
    # 空值项被忽略
    assert invert_synonym_map({"": "b", "c": ""}) == {}
    # 自映射只产生单形态组，不会形成 (x OR x)；实际扩展由 len(forms) > 1 兜住
    assert invert_synonym_map({"a": "a"}) == {"a": ["a"]}


def test_query_builder_parse_extra_synonyms():
    from app.services.fetchers.query_builder import parse_extra_synonyms

    parsed = parse_extra_synonyms(" PU=polyurethane , coat=coating ,broken, ,x=x")
    assert parsed == {"pu": "polyurethane", "coat": "coating"}
    assert parse_extra_synonyms("") == {}
    assert parse_extra_synonyms(None) == {}


def test_query_builder_raw_mode_is_verbatim():
    """默认 raw 模式必须逐字透传——这是与引入本模块前行为一致的保证。"""
    from app.services.fetchers.query_builder import build_query_plan

    # 含布尔运算符也不解释，raw 模式短路在最前面
    for q in ("waterborne polyurethane", 'elmo AND "sesame street"', "  spaced  out  "):
        plan = build_query_plan(q, mode="raw", base_synonyms={"aqueous": "waterborne"})
        assert plan.variants == (q.strip(),)
        assert plan.mode == "raw"
        assert plan.is_multi is False
    # 未知 mode 一律回落 raw，不静默变激进
    assert build_query_plan("x", mode="whatever").mode == "raw"
    assert build_query_plan("", mode="expanded").variants == ()


def test_query_builder_expanded_builds_or_groups():
    """同义词命中 → 该词展开成 OR 组；未命中的词保持单词，组间 AND。"""
    from app.services.content.relevance import _SYNONYMS
    from app.services.fetchers.query_builder import build_query_plan

    plan = build_query_plan("aqueous polyurethane coating", mode="expanded", base_synonyms=_SYNONYMS)
    assert plan.mode == "expanded"
    assert plan.variants == (
        '(aqueous OR "water-borne" OR waterbased OR waterborne) AND polyurethane AND coating',
    )
    # 用户原词排首位，便于肉眼确认扩展没有丢掉他写的词
    assert _group_terms(plan.variants[0])[0] == "aqueous"


def test_query_builder_boolean_is_expert_passthrough():
    """用户自己写了布尔运算符 → 不猜意图，原样透传。"""
    from app.services.fetchers.query_builder import build_query_plan

    q = '(elmo AND "sesame street") NOT (cookie OR monster)'
    plan = build_query_plan(q, mode="expanded", base_synonyms={"elmo": "elmo2"})
    assert plan.mode == "expert_passthrough"
    assert plan.variants == (q,)
    assert "专家模式" in plan.note


def test_query_builder_keeps_user_quoted_phrase():
    """用户手写的引号短语是原子词：保留引号、不拆开做同义词扩展。"""
    from app.services.fetchers.query_builder import build_query_plan

    plan = build_query_plan('"waterborne polyurethane" coating', mode="expanded", base_synonyms={})
    assert plan.variants == ('"waterborne polyurethane" AND coating',)


def test_query_builder_quotes_unsafe_tokens():
    from app.services.fetchers.query_builder import quote_term

    assert quote_term("polyurethane") == "polyurethane"
    assert quote_term("water-borne") == '"water-borne"'
    assert quote_term("multi word") == '"multi word"'
    assert quote_term("OR") == '"OR"'          # 保留词必须引号，否则被当语法
    assert quote_term("  ") == ""


def test_query_builder_never_quotes_cjk():
    """中文绝不加引号。

    实测（2026-09，真实 OpenAlex）：`水性聚氨酯涂层` 裸串 1593 命中，
    加双引号后 **0 命中** —— 引号让整串变成一个词条，在词干化 search 里匹配不到。
    这是"ASCII 中心假设"在中文上的静默失效，必须锁回归。
    """
    from app.services.fetchers.query_builder import build_query_plan, quote_term

    assert quote_term("水性聚氨酯涂层") == "水性聚氨酯涂层"
    assert quote_term("聚氨酯") == "聚氨酯"

    # 未命中同义词表 → 扩展开关对中文是安全 no-op（与 raw 完全同串）
    plan = build_query_plan("水性聚氨酯涂层", mode="expanded", base_synonyms={})
    assert plan.variants == ("水性聚氨酯涂层",)
    assert build_query_plan("水性聚氨酯涂层", mode="raw").variants == plan.variants


def test_query_builder_splits_over_url_budget():
    """超 URL 预算时切块，且各变体的词项并集与原式完全一致（等价性）。"""
    from app.services.fetchers.query_builder import (
        build_query_plan,
        encoded_len,
        render_query,
    )

    syn = {f"syn{i}": "base" for i in range(20)}
    expected = sorted(["base"] + [f"syn{i}" for i in range(20)])
    plan = build_query_plan("base", mode="expanded", base_synonyms=syn, max_encoded_len=60, max_variants=16)

    assert len(plan.variants) > 1          # 确实切了
    assert "并集" in plan.note
    for v in plan.variants:
        assert encoded_len(v) <= 60        # 每段都在预算内
    # 并集等价：把所有变体拆回词项，应恰好还原原 OR 组
    terms: list[str] = []
    for v in plan.variants:
        terms.extend(_group_terms(v))
    assert sorted(terms) == expected
    # 且任一变体都不是原式（确实拆分过）
    assert render_query([expected]) not in plan.variants


def test_query_builder_digest_tracks_synonym_changes():
    """digest 用于缓存键：同义词表变了、结果变了，digest 必须跟着变。"""
    from app.services.fetchers.query_builder import build_query_plan

    a = build_query_plan("aqueous coating", mode="expanded", base_synonyms={})
    b = build_query_plan("aqueous coating", mode="expanded", base_synonyms={"aqueous": "waterborne"})
    assert a.digest != b.digest
    # 同输入重复构造必须稳定（否则缓存永不命中）
    assert b.digest == build_query_plan(
        "aqueous coating", mode="expanded", base_synonyms={"aqueous": "waterborne"}
    ).digest


def test_search_settings_default_to_old_behaviour():
    """默认必须是 raw + 空扩展表 —— 升级后不改配置就等于行为不变。"""
    from app.config import Settings

    s = Settings(_env_file=None)
    assert s.search_query_mode == "raw"
    assert s.search_extra_synonyms == ""
    assert s.openalex_api_key == ""


# ---- 源不可用的降级语义（fetchers/base.py）----


def _offline_fetcher(handler):
    """构造一个把请求交给 handler 的 OpenAlexFetcher（离线，不发真请求）。"""
    import httpx

    from app.services.fetchers.openalex import OpenAlexFetcher

    f = OpenAlexFetcher(mailto="t@example.com")
    f._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return f


def test_source_unavailable_user_messages():
    """每种不可用都要有一句能直接显示给用户的人话。"""
    from app.services.fetchers.base import SourceUnavailable

    budget = SourceUnavailable(
        "openalex", "今日请求额度已用尽", kind="budget_exhausted", retry_after=3600
    )
    assert "额度已用尽" in budget.user_message()
    assert "1.0 小时" in budget.user_message()
    assert "OPENALEX_API_KEY" in budget.user_message()

    # 拿不到 retry-after 时也要给恢复提示且不崩
    assert "UTC 午夜" in SourceUnavailable("openalex", "x", kind="budget_exhausted").user_message()

    assert "限流" in SourceUnavailable("openalex", "x", kind="rate_limited").user_message()
    assert "服务端" in SourceUnavailable("openalex", "x", kind="server_error").user_message()
    assert "连接失败" in SourceUnavailable("openalex", "x", kind="timeout").user_message()
    assert "没有这条记录" in SourceUnavailable("openalex", "x", kind="not_found").user_message()
    assert "不可用" in SourceUnavailable("crossref", "什么都没说").user_message()


def test_retry_after_parsing_accepts_seconds_and_http_date():
    import time
    from email.utils import formatdate

    import httpx

    from app.services.fetchers.base import _retry_after_seconds

    assert _retry_after_seconds(httpx.Response(429, headers={"retry-after": "37222"})) == 37222
    assert _retry_after_seconds(httpx.Response(429, headers={})) is None
    # 头值不可解析（httpx 只收 ASCII 头值，故用 ASCII 垃圾串）
    assert _retry_after_seconds(httpx.Response(429, headers={"retry-after": "garbage"})) is None
    # HTTP-date 形态也要能解析
    future = formatdate(time.time() + 600, usegmt=True)
    v = _retry_after_seconds(httpx.Response(429, headers={"retry-after": future}))
    assert v is not None and 500 < v <= 600


def test_fetcher_get_returns_json_on_success():
    import asyncio

    import httpx

    f = _offline_fetcher(lambda r: httpx.Response(200, json={"ok": 1}))
    try:
        assert asyncio.run(f._get("https://api.openalex.org/works")) == {"ok": 1}
    finally:
        asyncio.run(f._client.aclose())


def test_fetcher_budget_exhausted_does_not_retry():
    """配额耗尽（429 + x-ratelimit-remaining:0）必须立即失败、不重试。

    依据 OpenAlex 官方降级表：日配额耗尽时本地重试不可能成功，只会白烧额度。
    实测 2026-09：免费档 $0.10/天用尽后 retry-after ≈ 37222 秒。
    """
    import asyncio

    import httpx

    from app.services.fetchers.base import SourceUnavailable

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"x-ratelimit-remaining": "0", "retry-after": "37222"},
            json={"error": "Rate limit exceeded"},
        )

    f = _offline_fetcher(handler)
    try:
        try:
            asyncio.run(f._get("https://api.openalex.org/works", {"search": "x"}))
            raise AssertionError("应抛 SourceUnavailable")
        except SourceUnavailable as e:
            assert e.kind == "budget_exhausted"
            assert e.retry_after == 37222
            assert "约 10.3 小时" in e.user_message()
        assert calls["n"] == 1, f"配额耗尽不应重试，实际发了 {calls['n']} 次"
    finally:
        asyncio.run(f._client.aclose())


def test_fetcher_burst_429_retries_then_reports_rate_limited():
    """突发限流（429 但没有 remaining:0）应退避重试，用尽后归类 rate_limited。"""
    import asyncio

    import httpx

    from app.services.fetchers.base import SourceUnavailable

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "too many"})

    f = _offline_fetcher(handler)
    try:
        try:
            asyncio.run(f._get("https://api.openalex.org/works", {"search": "x"}))
            raise AssertionError("应抛 SourceUnavailable")
        except SourceUnavailable as e:
            assert e.kind == "rate_limited"
        assert calls["n"] == 3, f"突发限流应重试到 3 次，实际 {calls['n']}"
    finally:
        asyncio.run(f._client.aclose())


def test_fetcher_404_becomes_not_found():
    """404 单独归类：别把"没这条记录"说成上游故障。"""
    import asyncio

    import httpx

    from app.services.fetchers.base import SourceUnavailable

    f = _offline_fetcher(lambda r: httpx.Response(404, json={}))
    try:
        try:
            asyncio.run(f._get("https://api.openalex.org/works/doi:10.1/x"))
            raise AssertionError("应抛 SourceUnavailable")
        except SourceUnavailable as e:
            assert e.kind == "not_found"
    finally:
        asyncio.run(f._client.aclose())


# ---- 语义模型预热：请求路径不得触发加载（relevance.warm_up / allow_load）----


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


def test_openalex_headers_property():
    from app.config import Settings

    assert Settings(openalex_api_key="").openalex_headers == {}
    assert Settings(openalex_api_key="K123").openalex_headers == {
        "Authorization": "Bearer K123"
    }


def test_journal_stats_sends_api_key_header(monkeypatch):
    """期刊 2yr_mean 的直连请求必须带 key。

    踩过的坑（2026-09-16 端到端复跑）：只有抓取器带了 key，journal_stats /
    _fetch_cited_5yr / _fetch_ref_years 这些裸 httpx 调用没带 → 走无 key 额度被 429
    → journal_2yr_mean 51/51 全空 → 期刊维度（权重 0.18）整批"缺数据"
    → 档位全部被"缺数据封顶"卡在"谨慎引用"，没有一篇能到"可选引用"。
    """
    import httpx

    from app.config import Settings
    from app.services import journal_stats

    seen: dict = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"summary_stats": {"2yr_mean_citedness": 3.3}}

    def _fake_get(url, params=None, headers=None, timeout=None):
        seen["headers"] = headers
        return _Resp()

    monkeypatch.setattr(httpx, "get", _fake_get)
    monkeypatch.setattr(journal_stats, "get_settings", lambda: Settings(openalex_api_key="K"))
    journal_stats.cache.clear()

    assert journal_stats.get_2yr_mean("https://openalex.org/S1") == 3.3
    assert seen["headers"] == {"Authorization": "Bearer K"}


def test_citation_graph_and_project_network_send_api_key(monkeypatch):
    """引用网络的两处直连也要带 key（否则引用树/项目网络在无 key 额度下静默缺失）。"""
    from app.config import Settings
    from app.services import citation_graph, project_network

    for mod in (citation_graph, project_network):
        monkeypatch.setattr(mod, "get_settings", lambda: Settings(openalex_api_key="K"))
        assert mod.get_settings().openalex_headers == {"Authorization": "Bearer K"}


# ---- 数据溯源：指纹（数据变没变）+ 完整性（算的时候够不够）----


def _fake_paper(**kw):
    """最小 paper 替身：provenance 只按属性名取值。"""

    class _Project:
        research_topic = "waterborne polyurethane"
        keywords = ["coating", "crosslinking"]

    class _Paper:
        id = "p1"
        title = "A study"
        abstract = "abstract text"
        fulltext_path = None
        openalex_work_id = "https://openalex.org/W1"
        openalex_source_id = "https://openalex.org/S1"
        work_type = "article"
        is_retracted = False
        is_classic = False
        journal_2yr_mean = 3.0
        journal_percentile = 50.0
        impact_factor = None
        jcr_quartile = None
        cas_zone = None
        cited_by_count = 10
        cited_by_5yr = 2
        self_citation_count = 1
        reference_count = 30
        citation_percentile = 60.0
        publication_year = 2020
        subfield_id = "1605"
        field_id = "16"
        corresponding_h_index = 12.0
        has_data_availability_stmt = False
        has_code_repo = False
        has_supplement = False
        project = _Project()

    p = _Paper()
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _fake_ev(fingerprint, data_status=None):
    class _Ev:
        pass

    ev = _Ev()
    ev.data_fingerprint = fingerprint
    ev.data_status = data_status
    ev.composite_score = 50.0
    ev.tier = "conditional"
    return ev


def test_provenance_fingerprint_ignores_non_scoring_fields():
    """非打分字段（卷期页）变动不该让评估变过时。"""
    from app.services import provenance

    a = provenance.compute_fingerprint(_fake_paper())
    b = provenance.compute_fingerprint(_fake_paper(volume="12", issue="3", pages="1-9"))
    assert a == b


def test_provenance_fingerprint_ignores_keyword_order():
    """关键词顺序不影响打分 → 排序后再哈希，避免误判过时。"""

    class _P:
        research_topic = "t"
        keywords = ["a", "b"]

    class _P2:
        research_topic = "t"
        keywords = ["b", "a"]

    from app.services import provenance

    assert provenance.compute_fingerprint(
        _fake_paper(project=_P())
    ) == provenance.compute_fingerprint(_fake_paper(project=_P2()))


def test_provenance_fingerprint_ignores_float_noise():
    """浮点末位抖动（同一次查询的表示差异）不该判成数据变了。"""
    from app.services import provenance

    a = provenance.compute_fingerprint(_fake_paper(journal_2yr_mean=18.84290903087337))
    b = provenance.compute_fingerprint(_fake_paper(journal_2yr_mean=18.84290903087338))
    assert a == b


def test_provenance_detects_the_real_regression():
    """2026-09-16 的真实场景：期刊指标从"空"变成"有值"必须判为数据已变。

    当时 key 没覆盖直连调用 → journal_2yr_mean 51/51 全空 → 档位被"缺数据封顶"卡死，
    而 status 全是 done，只能靠手动 force + offset 重跑定位。
    """
    from app.services import provenance

    degraded = provenance.compute_fingerprint(_fake_paper(journal_2yr_mean=None))
    fixed = provenance.compute_fingerprint(_fake_paper(journal_2yr_mean=18.84))
    assert degraded != fixed

    # 评估是在 degraded 数据上算的，现在数据变成 fixed → 必须报"数据已变"
    ev = _fake_ev(degraded, data_status={"journal": "missing", "citation": "ok"})
    assert provenance.recheck_reason(_fake_paper(journal_2yr_mean=18.84), ev) == "data_changed"
    # 数据没变则不算 actionable（但 journal 缺数据 → 仍然不可全信）
    assert provenance.recheck_reason(_fake_paper(journal_2yr_mean=None), ev).startswith("missing_data:")
    assert provenance.is_actionable(_fake_paper(journal_2yr_mean=None), ev) is False
    assert provenance.is_stale(_fake_paper(journal_2yr_mean=None), ev) is True


def test_provenance_detects_rules_and_topic_change(monkeypatch):
    """改 scoring_rules 权重、或改项目研究主题，都必须让既有分数变过时。"""
    from app.services import provenance

    base = provenance.compute_fingerprint(_fake_paper())

    monkeypatch.setattr(provenance, "get_rules", lambda: {"version": "v8", "weights": {"x": 2}})
    assert provenance.compute_fingerprint(_fake_paper()) != base

    monkeypatch.undo()
    base = provenance.compute_fingerprint(_fake_paper())

    class _P:
        research_topic = "换了个完全不同的课题"
        keywords = ["coating"]

    assert provenance.compute_fingerprint(_fake_paper(project=_P())) != base


def test_provenance_reports_missing_dimensions_not_as_actionable():
    """源头本来就缺数据（如三源都查不到的中文 DOI）→ 不喂给自动重跑，但如实上报。"""
    from app.services import provenance

    fp = provenance.compute_fingerprint(_fake_paper())
    ev = _fake_ev(
        fp,
        data_status={
            "journal": "missing",
            "citation": "missing",
            "timeliness": "ok",
            "author": "missing",
            "reproducibility": "missing",
            "content_quality": "missing",
        },
    )
    assert provenance.missing_dimensions(ev) == [
        "journal", "citation", "author", "reproducibility", "content_quality",
    ]
    assert provenance.recheck_reason(_fake_paper(), ev) == (
        "missing_data:journal,citation,author,reproducibility,content_quality"
    )
    assert provenance.is_actionable(_fake_paper(), ev) is False


def test_provenance_all_ok_is_trustworthy():
    """六维齐全 + 指纹一致 → 无需重评。"""
    from app.services import provenance

    fp = provenance.compute_fingerprint(_fake_paper())
    ev = _fake_ev(fp, data_status={d: "ok" for d in provenance.CRITICAL_DIMENSIONS})
    assert provenance.recheck_reason(_fake_paper(), ev) is None
    assert provenance.is_actionable(_fake_paper(), ev) is False
    assert provenance.is_stale(_fake_paper(), ev) is False


def test_provenance_old_evaluation_without_fingerprint_is_actionable():
    """本功能上线前的旧评估没有指纹 → 视为需重评，而不是"没问题"。"""
    from app.services import provenance

    assert provenance.recheck_reason(_fake_paper(), _fake_ev(None)) == "no_fingerprint"
    assert provenance.is_actionable(_fake_paper(), _fake_ev(None)) is True
    assert provenance.recheck_reason(_fake_paper(), None) == "no_evaluation"


def test_provenance_detects_fulltext_added_or_file_missing(tmp_path):
    """全文从"没有"到"有"（或记录在案但文件丢了）都要能识别。"""
    from app.services import provenance

    none_fp = provenance.compute_fingerprint(_fake_paper(fulltext_path=None))

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4" + b"0" * 500)
    has_fp = provenance.compute_fingerprint(_fake_paper(fulltext_path=str(pdf)))
    assert none_fp != has_fp

    gone_fp = provenance.compute_fingerprint(
        _fake_paper(fulltext_path=str(tmp_path / "missing.pdf"))
    )
    assert gone_fp != none_fp, "记录在案但文件不在，应与『没有全文』区分"
    assert gone_fp != has_fp
