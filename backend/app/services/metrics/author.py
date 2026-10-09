"""作者权重：学科标准化 H-index + 一作/通讯混合。

w₄ = 100 × H_author / (H_author + H_median_field)
- H_author：通讯作者 h-index（无标记时用一作）
- H_median_field：该学科活跃研究者 h-index 中位数（校准，化学领域 13——2026-08-09
  通讯/一作作者代表采样重算，破除被引降序精英偏置；原 27 虚高）
- H=中位数 → 50 中性；单调、无饱和（v3 的 min(H/中位,100) 在 H≥中位 即满分，
  失去区分度——h-index 右偏，中位以上的研究者大量顶格）。

作者顺序修正：通讯作者 ≠ 第一作者时，
    w₄ = 0.7×w₄_通讯 + 0.3×w₄_一作
原理：通讯负责设计与把关，一作执行实验，共同贡献可靠性。

无任何 h-index 数据 → 中性 50（不惩罚，OpenAlex works 列表常缺此字段）。
"""
from __future__ import annotations

from app.services import distributions
from app.services.rules import get_rules


def compute(paper) -> dict:
    rules = get_rules()
    w_corr = float(rules["author"]["corresponding_weight"])
    w_first = float(rules["author"]["first_author_weight"])
    authors = paper.authors or []
    field_median = distributions.h_index_mean_for(paper.subfield_id)
    # 学科校准缺失 → 标注降级（field_h_index.json 为空，h 中位数是全局基准）
    degraded = not distributions.h_index_calibrated()
    degraded_note = "（学科 h-index 校准缺失，使用全局基准）" if degraded else ""

    def _score(h) -> float | None:
        if h is None:
            return None
        if field_median <= 0:
            return 50.0
        return 100.0 * h / (h + field_median)

    first_h = authors[0].get("h_index") if authors else None
    corr_idx = next((i for i, a in enumerate(authors) if a.get("is_corresponding")), 0)
    corr_h = authors[corr_idx].get("h_index") if authors else None

    s_corr = _score(corr_h)
    s_first = _score(first_h)

    if s_corr is None and s_first is None:
        return {
            "score": 50.0,
            "basis": f"无作者 h-index 数据{degraded_note}",
            "author_h_index": None,
            "field_median": field_median,
            "data_status": "missing",
        }

    # 一作与通讯是不同人 → 加权混合
    if corr_idx != 0 and s_corr is not None and s_first is not None:
        score = w_corr * s_corr + w_first * s_first
        basis = (
            f"0.7×通讯(h={corr_h}, {s_corr:.0f}) + 0.3×一作(h={first_h}, {s_first:.0f}), "
            f"领域 h 中位数={field_median:.0f}{degraded_note}"
        )
    else:
        score = s_corr if s_corr is not None else s_first
        h = corr_h if s_corr is not None else first_h
        basis = (
            f"作者 h-index={h}, 领域 h 中位数={field_median:.0f} → "
            f"100×{h}/({h}+{field_median:.0f})={score:.0f}{degraded_note}"
        )

    return {
        "score": round(score, 1),
        "basis": basis,
        "author_h_index": corr_h,
        "first_author_h_index": first_h,
        "field_median": field_median,
        "data_status": "ok",
    }
