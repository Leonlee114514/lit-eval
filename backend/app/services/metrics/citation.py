"""被引权重：同年百分位优先，对数正态 z-score 兜底。

口径（v4）：
    ① 有 OpenAlex cited_by_percentile_year（同年被引百分位，官方精确值）→ 直接用
    ② 无被引计数（cited_by_count=None）→ 缺失：中性 50、data_status=missing
       （v4：缺数据=未知，不进决策门；v3 的 C=0 塌缩把"缺失"误当"被引=0"）
    ③ 无百分位且 C>0 → 对数正态 Φ((ln(C) - μ_ln) / σ_ln)，μ/σ = 该学科论文被引
       ln 分布的均值/标准差（校准）。被引分布右偏幂律，取 ln 近似正态，
       z-score 相对位置消除幂律右偏的极端杠杆，且不饱和。
    ④ C=0（新论文，真实数据）→ 0 分、data_status=ok
    ⑤ 无校准参数 → 中性 50、data_status=missing

综述打折（v5.1）：综述（work_type="review"）因被作背景引用而存在被引泡沫——
被引高分不必然反映原创贡献。评分后乘 `citation.review_discount`（默认 0.7）。

自引折算（v5.1）：自引率 = self_citation_count / reference_count（Crossref 反向口径）。
自引率 > ratio_gt（默认 0.20）→ 按超量比例线性折算，封顶 max_discount（默认 0.5）。

两项均在产生真实分数的路径应用（percentile/lognormal），缺失/零分不重复打折。

近5年被引（cited_by_5yr）作为趋势信号进 basis，不混合进分数（保持口径单一）。
"""
from __future__ import annotations

import math

from app.services import distributions
from app.services.rules import get_rules


def _adjusted(paper, score: float, basis: str, rules) -> tuple[float, str]:
    """被引泡沫修正：综述打折 + 自引率折算。返回 (score, basis)。"""
    if paper.work_type == "review":
        d = rules["citation"]["review_discount"]
        score *= d
        basis += f"，综述被引打 {d} 折（被引泡沫修正）"

    self_cites = getattr(paper, "self_citation_count", None)
    ref_total = getattr(paper, "reference_count", None)
    if self_cites is not None and ref_total:
        ratio = self_cites / ref_total
        cfg = rules["citation"]["self_citation"]
        if ratio > cfg["ratio_gt"]:
            over = min(1.0, (ratio - cfg["ratio_gt"]) / (1.0 - cfg["ratio_gt"]))
            mult = 1.0 - over * cfg["max_discount"]
            score *= mult
            basis += f"，自引率 {ratio:.0%} 偏高，被引分折算 {mult:.2f}"
    return score, basis


def compute(paper, cited_by_5yr: int | None = None) -> dict:
    raw_cites = paper.cited_by_count
    C = raw_cites if raw_cites is not None else 0
    pct = paper.citation_percentile  # 0-100
    cited_5yr = cited_by_5yr or paper.cited_by_5yr

    base = {"cited_by_count": raw_cites, "cited_by_5yr": cited_5yr, "citation_percentile": pct}
    five_yr_note = f"，近5年被引 {cited_5yr}" if cited_5yr else ""

    # ① 同年被引百分位（官方精确值，优先）
    if pct is not None:
        rules = get_rules()
        score, basis = _adjusted(
            paper, float(pct), f"同年被引百分位 {pct}（OpenAlex 精确版）{five_yr_note}", rules
        )
        return {
            **base,
            "score": round(score, 1),
            "basis": basis,
            "method": "percentile_year",
            "data_status": "ok",
        }

    # ② 无被引计数 → 缺失（v4：缺数据=未知，非"被引=0"，不进决策门）
    if raw_cites is None:
        return {
            **base,
            "score": 50.0,
            "basis": "无被引数据",
            "method": "missing",
            "data_status": "missing",
        }

    # ③ 对数正态兜底
    if C > 0:
        params = distributions.citation_lognormal_for(paper.subfield_id)
        if params:
            mu, sigma = params
            score = distributions.lognormal_percentile(mu, sigma, C)
            if score is not None:
                z = (math.log(C) - mu) / sigma
                basis = (
                    f"总被引 {C} 在该学科被引对数正态分布的位置 z={z:.2f}（μ_ln={mu:.2f}, "
                    f"σ_ln={sigma:.2f}）→ Φ(z)={score}{five_yr_note}"
                )
                rules = get_rules()
                score, basis = _adjusted(paper, score, basis, rules)
                return {
                    **base,
                    "score": round(score, 1),
                    "basis": basis,
                    "method": "lognormal",
                    "z_score": round(z, 3),
                    "data_status": "ok",
                }
        return {
            **base,
            "score": 50.0,
            "basis": "无被引校准数据",
            "method": "missing",
            "data_status": "missing",
        }

    # ④ 新论文被引为 0（真实数据，非缺失）
    return {
        **base,
        "score": 0.0,
        "basis": "被引为 0（新论文）",
        "method": "zero",
        "data_status": "ok",
    }
