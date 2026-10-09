"""数据溯源单测：指纹（数据变没变）与完整性（算的时候够不够）。"""

from __future__ import annotations


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
