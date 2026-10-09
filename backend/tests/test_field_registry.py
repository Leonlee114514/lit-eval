"""字段注册表测试：一份字段知识，不许再散成八份。

守护 2026-10 review 的 Warning（Knowledge Duplication）：同一个字段清单过去散在
``models`` 列、``schemas.PaperPatch``、``_PATCHABLE_FIELDS``、``_detect_missing``、
``_apply_fetched``、``provenance._METRIC_FIELDS``、``_ensure_schema`` 里，
漏改任意一处都不报错，只是静默降级（抓到了没落库 / 指纹漏算该字段）。

这里把三条通道都钉住：注册表 ↔ 表的列、注册表 ↔ 抓取缺失判定、注册表 ↔ 指纹字段；
外加 ``apply_fetched`` 的赋值语义与 ``_ensure_schema`` 的自动迁移。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models.fields import (
    FETCHED_SPECS,
    FIELDS,
    REQUIRED_KEYS,
    SCORING_FIELDS,
    apply_fetched,
)
from app.models.paper import Paper


class _Bag:
    """能让 apply_fetched 落属性的最小替身（不需要 ORM）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_registry_covers_every_paper_column():
    """注册表必须与 papers 表的列完全一致 —— 新增列忘了登记会在这里失败。"""
    registered = {f.name for f in FIELDS}
    actual = {c.name for c in Paper.__table__.columns}
    assert actual - registered == set(), f"Paper 有列未登记: {sorted(actual - registered)}"
    assert registered - actual == set(), f"注册表登记了不存在的列: {sorted(registered - actual)}"


def test_required_keys_are_fetch_result_keys():
    """required 的字段必须来自抓取结果，否则永远判不出缺失。"""
    for key in REQUIRED_KEYS:
        spec = next(f for f in FIELDS if (f.source or f.name) == key)
        assert spec.fetched, f"{key} 标了 required 却不由抓取写入"


def test_detect_missing_follows_the_registry():
    """缺失判定与注册表同步：缺哪个报哪个。"""
    from app.services.fetchers.base import detect_missing

    assert detect_missing({k: "x" for k in REQUIRED_KEYS}) == []
    for key in REQUIRED_KEYS:
        partial = {k: "x" for k in REQUIRED_KEYS}
        partial[key] = None
        assert detect_missing(partial) == [key]
    # 空串与空列表同样算缺失
    for key in REQUIRED_KEYS:
        partial = {k: "x" for k in REQUIRED_KEYS}
        partial[key] = ""
        assert detect_missing(partial) == [key]


# 2026-10 重构前的 22 个指纹字段：搬进注册表时一个都不能丢
_LEGACY_METRIC_FIELDS = (
    "openalex_work_id", "openalex_source_id", "work_type", "is_retracted", "is_classic",
    "journal_2yr_mean", "journal_percentile", "impact_factor", "jcr_quartile", "cas_zone",
    "cited_by_count", "cited_by_5yr", "self_citation_count", "reference_count",
    "citation_percentile", "publication_year", "subfield_id", "field_id",
    "corresponding_h_index",
    "has_data_availability_stmt", "has_code_repo", "has_supplement",
)


def test_scoring_fields_keep_the_legacy_fingerprint_inputs():
    """指纹字段从手写元组搬进注册表，覆盖面不许缩小。"""
    assert len(_LEGACY_METRIC_FIELDS) == 22
    assert set(_LEGACY_METRIC_FIELDS) <= set(SCORING_FIELDS)


def _sample(spec):
    """按 coerce 类型给一个合法样本值。"""
    if spec.coerce == "date":
        return "2024-03-05"
    if spec.coerce == "list":
        return [{"n": 1}]
    return f"value-{spec.name}"


def test_apply_fetched_writes_every_fetched_field():
    """注册表里每个 fetched 字段都要真的被写进去（不能只登记不落库）。"""
    data = {spec.source or spec.name: _sample(spec) for spec in FETCHED_SPECS}
    bag = _Bag()
    apply_fetched(bag, data)

    for spec in FETCHED_SPECS:
        raw = data[spec.source or spec.name]
        if spec.coerce == "date":
            expected = date(2024, 3, 5)
        elif spec.coerce == "list":
            expected = raw
        else:
            expected = raw
        assert getattr(bag, spec.name) == expected, spec.name


def test_apply_fetched_keeps_existing_value_when_source_is_empty():
    """pmid 保底语义：本次抓不到不许把已有的抹成空。"""
    bag = _Bag(pmid="12345678")
    apply_fetched(bag, {"pmid": None})
    assert bag.pmid == "12345678"


def test_apply_fetched_coerces_empty_to_none_and_empty_list():
    """空值规范化：日期空 → None，列表空 → []（库里不存 None 列表）。"""
    bag = _Bag()
    apply_fetched(bag, {"publication_date": None, "authors": None, "keywords": None})
    assert bag.publication_date is None
    assert bag.authors == []
    assert bag.keywords == []


def test_patch_schema_fields_are_real_columns():
    """人工补全模型（PaperPatch）的字段必须是 Paper 的真列 —— 拼错就是"填了不落库"。"""
    from app.schemas.paper import PaperPatch

    columns = {c.name for c in Paper.__table__.columns}
    assert set(PaperPatch.model_fields) <= columns, (
        f"PaperPatch 里有不存在的列: {sorted(set(PaperPatch.model_fields) - columns)}"
    )


def test_ensure_schema_backfills_missing_columns(test_app, monkeypatch):
    """自动迁移：只建两列的 papers 表，_ensure_schema 应把其余列补齐。"""
    import app.db as dbmod
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE papers (id VARCHAR(36) PRIMARY KEY)"))
        conn.commit()

    monkeypatch.setattr(dbmod, "engine", engine)
    dbmod._ensure_schema()

    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(papers)"))}
    expected = {c.name for c in Paper.__table__.columns}
    assert expected <= columns, f"未补上的列: {sorted(expected - columns)}"


def test_ensure_schema_is_idempotent(test_app):
    """连续两次迁移不应报错（真实库每次启动都会走一遍）。"""
    import app.db as dbmod

    dbmod._ensure_schema()
    dbmod._ensure_schema()
