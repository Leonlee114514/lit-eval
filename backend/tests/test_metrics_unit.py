"""计量与聚合单测：期刊 / 被引 / 活跃度 / 可重复性 / 作者 / 聚合（全部离线）。"""

from __future__ import annotations

import math

from tests.fakes import _MiniPaper, _force_coverage


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
