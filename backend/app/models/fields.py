"""Paper 字段注册表 —— 抓取落库 / 缺失判定 / 打分指纹的唯一事实源。

为什么需要它：同一个字段清单过去散在 8 个地方 ——

    1. ``models/paper.py``            列定义（ORM 的根本，留着）
    2. ``schemas/paper.py``           接口模型（Pydantic，跨语言边界，留着）
    3. ``router_papers._PATCHABLE_FIELDS``   与 (2) 的 12 个字面完全相同
    4. ``fetchers/base._detect_missing``     必填白名单（5 个）
    5. ``evaluation_service._apply_fetched`` 23 行手工 ``paper.x = data.get("x")``
    6. ``provenance._METRIC_FIELDS``         指纹字段（22 个）
    7. ``db._ensure_schema``                 9 条手工迁移登记
    8. ``frontend/src/api/types.ts``         前端类型（跨语言，留着）

漏改任意一处的后果都不是报错，而是**静默降级**：抓到了没落库；或指纹漏算该字段，
导致"分数已经过时却仍显示可信"。2026-09-16 那次 51 篇全空（`journal_2yr_mean`
空值、档位被缺数据封顶、而 `status` 全是 `done`）就是这一类根因。

现在新增一个元数据字段，只改两处：本文件的 ``FIELDS`` 一行 + ``models/paper.py`` 一列
（Pydantic 视图与前端类型按需再加）。``tests/test_field_registry.py`` 会断言注册表
完整覆盖 Paper 的每一列 —— 忘了登记会在测试里失败，而不是在生产里静默丢数据。

``scoring=True`` 的字段进 ``provenance`` 指纹：改了就说明"这份分数基于的输入变了"。
``fetched=True`` 的字段由 ``apply_fetched()`` 按本表批量写入，替代逐行手工赋值。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


def _as_date(value: Any) -> date | None:
    """ISO 日期串 → date；空值或畸形返回 None（不抛）。"""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_list(value: Any) -> list:
    """空值 → 空列表（作者/关键词在库里统一是 list，不存 None）。"""
    return value or []


_COERCERS = {"date": _as_date, "list": _as_list}


@dataclass(frozen=True)
class PaperField:
    """Paper 的一列及其跨模块语义。

    name:          列名，也是抓取结果里的键（``source`` 不同时才需要单独写）
    source:        抓取结果 dict 里的键名；None 表示与 name 相同
    fetched:       由 ``apply_fetched()`` 通用路径写入（替代手工 ``paper.x = data.get("x")``）
    required:      抓取后必须存在，否则计入 ``missing_fields`` → 状态判 partial
    scoring:       参与打分，进 ``provenance`` 指纹
    coerce:        落库前转换，取值 "date" / "list"
    keep_existing: 新值为空时保留库中现值（首次抓不到、后续补上时不被空值抹掉）
    """
    name: str
    source: str | None = None
    fetched: bool = False
    required: bool = False
    scoring: bool = False
    coerce: str | None = None
    keep_existing: bool = False


# —— 完整覆盖 papers 表。顺序按语义分组，与 models/paper.py 的排布呼应 ——
FIELDS: tuple[PaperField, ...] = (
    # ---- 系统列（由流程/关系维护，不来自抓取、不进指纹）----
    PaperField("id"),
    PaperField("project_id"),
    PaperField("doi"),
    PaperField("fulltext_path"),
    PaperField("status"),
    PaperField("missing_fields"),
    PaperField("fetch_log"),
    PaperField("created_at"),
    PaperField("updated_at"),

    # ---- 元数据（三源合并后落库）----
    PaperField("pmid", fetched=True, keep_existing=True),
    PaperField("title", fetched=True, required=True),
    PaperField("authors", fetched=True, coerce="list"),
    PaperField("journal", fetched=True, required=True),
    PaperField("issn", fetched=True),
    PaperField("openalex_source_id", fetched=True, scoring=True),
    PaperField("openalex_work_id", fetched=True, scoring=True),
    PaperField("publication_year", fetched=True, required=True, scoring=True),
    PaperField("publication_date", fetched=True, coerce="date"),
    PaperField("abstract", fetched=True, required=True),
    PaperField("keywords", fetched=True, coerce="list"),
    # work_type 不走通用路径：需 detect_review(work_type, title, s2_work_type) 综合判断
    PaperField("work_type", scoring=True),
    PaperField("volume", fetched=True),
    PaperField("issue", fetched=True),
    PaperField("pages", fetched=True),

    # ---- 引用指标 ----
    PaperField("cited_by_count", fetched=True, required=True, scoring=True),
    PaperField("cited_by_5yr", fetched=True, scoring=True),
    PaperField("self_citation_count", fetched=True, scoring=True),
    PaperField("reference_count", fetched=True, scoring=True),
    PaperField("citation_percentile", fetched=True, scoring=True),
    PaperField("field_of_study", fetched=True),
    PaperField("subfield_id", fetched=True, scoring=True),
    PaperField("field_id", fetched=True, scoring=True),

    # ---- 期刊指标（impact_factor / jcr / cas 为人工补全，其余抓取或计算）----
    PaperField("impact_factor", scoring=True),
    PaperField("jcr_quartile", scoring=True),
    PaperField("cas_zone", scoring=True),
    PaperField("journal_2yr_mean", fetched=True, scoring=True),
    PaperField("journal_percentile", scoring=True),

    # ---- 作者 ----
    PaperField("corresponding_h_index", scoring=True),

    # ---- 可重复性（正文解析或人工补全）----
    PaperField("has_data_availability_stmt", scoring=True),
    PaperField("has_code_repo", scoring=True),
    PaperField("has_supplement", scoring=True),
    PaperField("is_retracted", scoring=True),
    PaperField("is_classic", scoring=True),
)

# 走 apply_fetched 通用落库路径的字段
FETCHED_SPECS: tuple[PaperField, ...] = tuple(f for f in FIELDS if f.fetched)

# 抓取后必须存在的键（缺了 → missing_fields → status=partial）
REQUIRED_KEYS: tuple[str, ...] = tuple(f.source or f.name for f in FIELDS if f.required)

# 参与打分、进指纹的字段
SCORING_FIELDS: tuple[str, ...] = tuple(f.name for f in FIELDS if f.scoring)


def apply_fetched(paper, data: dict) -> None:
    """按注册表把抓取结果写入 Paper。

    只写 ``fetched=True`` 的字段。两个特例不在此路径、由调用方处理：
    ``work_type``（需 detect_review 综合判断）、``missing_fields`` / ``fetch_log``（抓取流程自有语义）。
    """
    for spec in FETCHED_SPECS:
        value = data.get(spec.source or spec.name)
        if spec.coerce:
            value = _COERCERS[spec.coerce](value)
        if spec.keep_existing and not value:
            continue
        setattr(paper, spec.name, value)
