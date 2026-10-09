"""四步决策流程：筛期刊 → 看被引 → 读内容 → 查溯源。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services import distributions
from app.services.rules import get_rules
from app.services.scoring.weights import get_weights


def compute_tier(composite_score: float | None) -> str:
    """分数段 → 推荐档位：≥0.85 优先 / ≥0.60 可选 / ≥0.35 谨慎 / <0.35 不推荐。"""
    rules = get_rules()
    t = rules["tier"]
    s = (composite_score or 0.0) / 100
    if s >= float(t["high_priority"]):
        return "high_priority"
    if s >= float(t["recommended"]):
        return "recommended"
    if s >= float(t["conditional"]):
        return "conditional"
    return "not_recommended"


_TIER_ORDER = ["high_priority", "recommended", "conditional", "not_recommended"]


def _tier_down(tier: str) -> str:
    """低置信度时档位降一级；not_recommended 保持不动。"""
    try:
        i = _TIER_ORDER.index(tier)
    except ValueError:
        return tier
    return _TIER_ORDER[min(i + 1, len(_TIER_ORDER) - 1)]


def decide(
    paper,
    component_scores: dict[str, float],
    composite_score: float | None = None,
    relevance: float | None = None,
    data_status: dict[str, str] | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """返回 {decision, reasons, warnings, tier}。

    v4 调和：decision 纳入综合分档位 + 缺数据三态门。
    - tier：综合分分数段映射的推荐档位（主结论）
    - decision：四步规则门（筛期刊→看被引→读内容→查溯源）+ 综合分约束
        deep_read         = tier ≥ recommended 且 期刊门+被引门**确认通过**
        reject            = tier=not_recommended 且 双门**确认失败**
        background_only   = 其余（含任一关键门数据缺失=unknown）
    - data_status（v4）：{journal, citation, ...} 各分量 "ok"/"missing"。
      missing → 对应门判 unknown（既不通过也不失败：不进 deep_read、也不判 reject）；
      期刊/被引任一缺失且档位在 high_priority/recommended → 档位封顶 conditional + 告警
      （缺数据=未知，不是达标）。
    - reasons：四步门逐条分析，作决策理由
    composite_score 由 evaluate 传入（与 aggregate 同一来源，权重归一化一致）；
    不传时回退按归一化权重加权（兼容直接调用/测试）。
    relevance（0-1）为项目相关性：低于 relevance_low_threshold 时档位封顶
    "conditional"——顶刊高被引但与课题不搭，不应推荐深读（2026-08-02）。
    confidence（0-1）为元数据完整率（v6）：低于 confidence.low_threshold 时
    档位再降一级（not_recommended 不再降），作为缺失封顶的通用推广。
    """
    rules = get_rules()
    step1 = rules["decision"]["step1"]
    step2 = rules["decision"]["step2"]
    step3 = rules["decision"]["step3"]
    reasons: list[str] = []
    warnings: list[str] = []
    data_status = data_status or {}

    journal_percentile = paper.journal_percentile

    # ---- Step 0 争议性：撤稿 → 直接拒绝引用（最高优先级）----
    # 免费信号（OpenAlex is_retracted / Crossref update-to）在抓取阶段落库 paper.is_retracted；
    # 规则化 LLM 的 controversy 信号是辅助。撤稿文献无论其他指标多高都不建议引用。
    if getattr(paper, "is_retracted", False):
        return {
            "decision": "reject",
            "reasons": ["该文献已被标记撤稿，不建议引用"],
            "warnings": ["⚠ 该文献已被撤稿，请勿引用或引用时务必注明撤稿状态"],
            "tier": "not_recommended",
        }

    # ---- Step 1 筛期刊（2026-08-02 相对化：去掉 IF>3 绝对前置）----
    # JCR/中科院分区本身是学科内相对口径；期刊百分位是化学领域相对口径。
    # IF 仅作展示/告警，不再参与门槛判定。
    jcr_ok = bool(paper.jcr_quartile and paper.jcr_quartile.upper() in step1["jcr_acceptable"])
    cas_ok = bool(paper.cas_zone and paper.cas_zone <= step1["cas_zone_min"])
    percentile_ok = bool(
        journal_percentile is not None and journal_percentile >= step1["journal_percentile_ge"]
    )
    # v4 三态门：期刊数据缺失 → unknown（既不通过也不失败，不进 deep_read 也不判 reject）
    journal_unknown = data_status.get("journal") == "missing"
    if journal_unknown:
        journal_pass: bool | None = None
        reasons.append("期刊数据缺失，无法判定期刊门槛")
    else:
        journal_pass = (jcr_ok or cas_ok) or percentile_ok
        if journal_pass:
            reasons.append(
                f"期刊门槛通过（JCR/中科院分区或化学领域百分位≥{step1['journal_percentile_ge']} 达标）"
            )
        else:
            reasons.append("期刊门槛未达标，建议仅作背景引用")

    # ---- Step 2 看被引（2026-08-02 相对化：化学领域基准）----
    # 主信号：学科内被引分 ≥ 中位（citation_score_ge=50）。被引维度
    # （同年百分位 / 对数正态）本身就是学科相对口径，≥50 = 化学领域被引
    # 中位以上。兜底：老文献总被引 ≥ 化学领域经典阈值
    # （year_citation_median.json global，2026-08-07 起为非零被引 p90≈17，
    # 原 217 是被引降序偏置的虚高中位），或手动标记经典。
    age = (date.today().year - paper.publication_year) if paper.publication_year else None
    cited_total = paper.cited_by_count or 0
    citation_score = component_scores.get("citation", 0.0)
    median_cites = distributions.recent_citation_median_for(None)

    # v4 三态门：被引数据缺失 → unknown（缺数据=未知，不是"达标"）
    citation_unknown = data_status.get("citation") == "missing"
    if citation_unknown:
        citation_pass: bool | None = None
        reasons.append("被引数据缺失，无法判定被引门槛")
    else:
        fresh_ok = citation_score >= step2["citation_score_ge"]
        classic_ok = bool(
            age is not None
            and age > step2["classic_age_gt"]
            and cited_total >= median_cites
        )
        citation_pass = fresh_ok or classic_ok or bool(paper.is_classic)

        if citation_pass:
            reasons.append(
                f"被引数据达标（学科内被引分≥{step2['citation_score_ge']} 或经典，化学领域经典阈值 {median_cites:.0f}）"
            )
        else:
            reasons.append("被引偏低（未达化学领域经典阈值），需精读验证内容质量")

    # ---- Step 3 读内容（辅助信息，不阻塞） ----
    structure_score = component_scores.get("content_quality", 50.0)
    if data_status.get("content_quality") == "missing":
        reasons.append("摘要与全文均缺失，内容质量为中性占位（50/100），建议先补全文再精读")
    elif structure_score >= step3["content_quality_ge"]:
        reasons.append(f"内容质量良好（{structure_score:.0f}/100），值得精读")
    else:
        reasons.append(f"内容质量一般（{structure_score:.0f}/100），建议核实实验细节")

    # ---- 综合分档位（v3：decision 与 tier 同源，消除同屏矛盾）----
    if composite_score is None:
        weights = get_weights()  # 归一化权重，与 aggregate 一致
        composite_score = sum(
            component_scores.get(k, 50.0 if k == "content_quality" else 0.0) * w
            for k, w in weights.items()
        )
    tier = compute_tier(composite_score)

    # ---- 相关性约束（2026-08-02：与课题不搭 → 档位封顶 conditional）----
    relevance_capped = False
    if relevance is not None:
        low_thr = float(rules["decision"]["relevance_low_threshold"])
        if relevance < low_thr and tier in ("high_priority", "recommended"):
            tier = "conditional"
            relevance_capped = True

    # ---- 缺失数据约束（v4：缺数据=未知，关键门缺失 → 档位封顶 conditional）----
    # 全缺论文综合分≈50 会落入"可选引用"档，但关键证据缺失时不应推荐——
    # 与相关性封顶同理，只对高/可选档生效，not_recommended 保持原档。
    missing_capped = False
    if (journal_unknown or citation_unknown) and tier in ("high_priority", "recommended"):
        tier = "conditional"
        missing_capped = True
        warnings.append("⚠ 期刊/被引数据缺失，判定置信度低，档位封顶'谨慎引用'")

    # ---- 低置信度约束（v6：把"缺数据封顶"推广到元数据完整率）----
    # 低置信度论文的推荐结论更不可靠，档位整体降一级；not_recommended 不再降。
    confidence_downgraded = False
    conf_rules = rules.get("confidence", {})
    if confidence is not None and conf_rules.get("downgrade", True):
        low_thr = float(conf_rules.get("low_threshold", 0.5))
        if confidence < low_thr:
            new_tier = _tier_down(tier)
            if new_tier != tier:
                confidence_downgraded = True
                tier = new_tier
                warnings.append(
                    f"⚠ 元数据置信度 {confidence:.0%} 低于 {low_thr:.0%}，"
                    f"推荐档位已降级为「{rules['labels']['tier'][tier]}」"
                )

    # ---- 最终判定（四步门 + 综合分档位约束；v4 确认通过/确认失败语义）----
    # unknown（None）既不算通过也不算失败：不进 deep_read，也不判 reject。
    if tier in ("high_priority", "recommended") and journal_pass is True and citation_pass is True:
        decision = "deep_read"
    elif tier == "not_recommended" and journal_pass is False and citation_pass is False:
        decision = "reject"
    else:
        decision = "background_only"

    reasons.append(
        f"综合质量分 {composite_score:.1f}/100 → 档位「{tier}」，"
        f"{'达到精读档' if tier in ('high_priority','recommended') else '未达精读档'}"
    )
    if relevance_capped:
        reasons.append(
            f"相关性 {relevance:.2f} < {rules['decision']['relevance_low_threshold']}，与课题不搭，档位封顶'谨慎引用'"
        )
    if missing_capped:
        reasons.append("期刊/被引数据缺失，判定置信度低，档位封顶'谨慎引用'")
    if confidence_downgraded:
        reasons.append(
            f"元数据置信度 {confidence:.0%} 偏低，推荐档位已降一级"
        )

    # ---- Warnings ----
    # 综述被引泡沫告警线（2026-08-14 起相对化学领域经典阈值，替代旧绝对 1000）：
    # 综述被引 ≥ 经典线×review_citation_multiple，且不低于 floor，才算"被引极高"
    if paper.work_type == "review":
        _classic_line = distributions.recent_citation_median_for(None)
        _bubble_line = max(
            rules["warnings"]["review_citation_floor"],
            _classic_line * rules["warnings"]["review_citation_multiple"],
        )
        if cited_total >= _bubble_line:
            warnings.append(
                f"该文献为综述且被引 {cited_total} ≥ 告警线 {_bubble_line:.0f}，"
                "注意被引泡沫现象——原创贡献可能低于同被引的原创研究"
            )
    if _self_citation_ratio(paper) > rules["warnings"]["self_citation_ratio_gt"]:
        warnings.append("自引比例偏高，被引数据需谨慎解读")
    if paper.impact_factor and paper.journal_percentile is not None:
        gap = abs(paper.journal_percentile - _if_proxy_percentile(paper.impact_factor))
        if gap > rules["warnings"]["percentile_vs_if_gap_gt"]:
            warnings.append("期刊百分位与原始影响因子差异大，注意 IF 为跨学科混合口径、百分位为学科内口径")
    # 被引趋势：近5年被引占比过低 → 引用增长趋缓或为历史累积
    if paper.cited_by_5yr and paper.cited_by_count and paper.cited_by_count > 100:
        ratio = paper.cited_by_5yr / paper.cited_by_count
        if ratio < 0.10:
            warnings.append(
                f"近5年被引占总量仅 {ratio:.0%}，引用增长趋缓或为历史累积，活跃度参考价值有限"
            )

    # ---- Step 4 查溯源（固定输出） ----
    reasons.append(f"溯源：DOI {paper.doi or '未知'}；出版方/全文链接见报告 source_trace；引用时优先该原始出处")

    return {"decision": decision, "reasons": reasons, "warnings": warnings, "tier": tier}


def _self_citation_ratio(paper) -> float:
    """自引占比：参考自引条数 / 参考文献总数（Crossref 反向口径，v3.1）。

    自引率已落库 Paper.self_citation_count / reference_count（Crossref fetcher 抓取）；
    无数据（Crossref 无 reference 或作者缺失）返回 0（不告警，避免误报）。
    """
    self_cites = getattr(paper, "self_citation_count", None)
    ref_total = getattr(paper, "reference_count", None)
    if self_cites is not None and ref_total:
        return self_cites / ref_total
    return 0.0


def _if_proxy_percentile(if_value: float) -> float:
    """把原始 IF 粗略换算成百分位（全局基准），仅用于 gap 告警。"""
    # 假设 IF 大致服从 log 分布：p ≈ 100*(1 - exp(-if/6))
    import math

    return min(100.0, 100.0 * (1 - math.exp(-if_value / 6.0)))
