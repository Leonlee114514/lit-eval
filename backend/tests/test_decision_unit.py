"""决策与规则单测：门槛、档位、封顶与降级、撤稿。（门槛值的配置来源另见 test_decision_config.py）"""

from __future__ import annotations

from tests.fakes import _MiniPaper


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
