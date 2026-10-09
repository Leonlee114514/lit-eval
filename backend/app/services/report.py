"""综合报告生成：结构化 JSON + Markdown 导出。"""
from __future__ import annotations

from app.models.evaluation import Evaluation
from app.models.paper import Paper
from app.services.rules import get_rules


def generate(paper: Paper, evaluation: Evaluation) -> dict:
    rules = get_rules()
    labels = rules.get("labels", {})

    from app.services.citation_formats import format_all

    return {
        "paper_id": paper.id,
        "title": paper.title,
        "doi": paper.doi,
        "journal": paper.journal,
        "publication_year": paper.publication_year,
        "paper_summary": {
            "authors": paper.authors or [],
            "keywords": paper.keywords or [],
            "field_of_study": paper.field_of_study,
            "work_type": paper.work_type,
            "cited_by_count": paper.cited_by_count,
            "cited_by_5yr": paper.cited_by_5yr,
            "impact_factor": paper.impact_factor,
            "jcr_quartile": paper.jcr_quartile,
            "cas_zone": paper.cas_zone,
            "journal_percentile": paper.journal_percentile,
            "abstract": paper.abstract,
            "is_retracted": paper.is_retracted,
        },
        "component_scores": evaluation.component_scores or {},
        "radar_scores": evaluation.radar_scores or {},
        "composite_score": evaluation.composite_score,
        "confidence": evaluation.confidence,
        "decision": evaluation.decision,
        "tier": evaluation.tier,
        "decision_reasons": evaluation.decision_reasons or [],
        "content_eval": {
            "abstract_structure": evaluation.abstract_structure or {},
            "relevance": evaluation.relevance or {},
            "reproducibility": evaluation.reproducibility or {},
            "figure_analysis": evaluation.figure_analysis or {},
        },
        "llm_assessment": evaluation.llm_assessment,
        "citation_intent": evaluation.citation_intent,
        "warnings": evaluation.warnings or [],
        "citation_formats": format_all(paper),
        "source_trace": paper.fetch_log or [],
        "labels": {
            "decision": labels.get("decision", {}).get(evaluation.decision, evaluation.decision),
            "tier": labels.get("tier", {}).get(evaluation.tier, evaluation.tier),
            "components": labels.get("components", {}),
            "radar": labels.get("radar", {}),
        },
    }


def to_markdown(report: dict) -> str:
    """Markdown 导出：便于本地存档/协作审阅。"""
    lines: list[str] = []
    decision_label = report["labels"]["decision"]
    tier_label = report["labels"]["tier"]
    components_label = report["labels"]["components"]
    radar_label = report["labels"]["radar"]

    lines.append(f"# 文献评估报告\n")
    lines.append(f"**{report['title']}**\n")
    lines.append(f"- DOI: `{report['doi'] or '未知'}`")
    lines.append(f"- 期刊: {report['journal'] or '未知'}（{report['publication_year'] or '未知'}）")
    _conf = report.get("confidence")
    _conf_text = f"{_conf:.0%}" if isinstance(_conf, (int, float)) else "未知"
    lines.append(f"- 综合质量分: **{report['composite_score']}/100**（置信度 {_conf_text}）")
    lines.append(f"- 推荐档位: **{tier_label}**")
    lines.append(f"- 决策: **{decision_label}** ({report['decision']})\n")

    cites = report.get("citation_formats") or {}
    if any(cites.values()):
        lines.append("## 标准引用格式\n")
        style_label = {"apa": "APA (7th)", "mla": "MLA (9th)", "gbt7714": "GB/T 7714"}
        for key, label in style_label.items():
            val = cites.get(key)
            if val:
                lines.append(f"**{label}**\n\n    {val}\n")
        lines.append("")

    lines.append("## 五维雷达分\n")
    lines.append("| 维度 | 分数 |")
    lines.append("| --- | --- |")
    for key, label in radar_label.items():
        lines.append(f"| {label} | {report['radar_scores'].get(key, '-')}/100 |")
    lines.append("")

    lines.append("## 分量得分\n")
    lines.append("| 分量 | 分数 |")
    lines.append("| --- | --- |")
    for key, label in components_label.items():
        lines.append(f"| {label} | {report['component_scores'].get(key, '-')}/100 |")
    lines.append("")

    lines.append("## 决策依据\n")
    for reason in report["decision_reasons"]:
        lines.append(f"- {reason}")
    lines.append("")

    if report["warnings"]:
        lines.append("## ⚠ 注意事项\n")
        for w in report["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    content = report["content_eval"]
    lines.append("## 内容评估\n")
    lines.append(f"- 摘要结构分: {content['abstract_structure'].get('score', '-')}/100")
    lines.append(f"- 与研究主题相关性: {content['relevance'].get('score', '-')}（{content['relevance'].get('method', '-')}）")
    lines.append(f"- 可重复性: {content['reproducibility'].get('score', '-')}/100")
    fa = content.get("figure_analysis", {})
    if fa:
        score = fa.get("score")
        lines.append(
            f"- 图表质量: {score if score is not None else '-'}"
            f"（图 {fa.get('total_figures', 0)} 张：数据图 {fa.get('data_figures', 0)} / "
            f"示意图 {fa.get('schematic_figures', 0)} / 未分类 {fa.get('unclassified_figures', 0)}，"
            f"表 {fa.get('tables', 0)} 张）"
        )
    lines.append("")

    llm = report["llm_assessment"]
    if llm:
        lines.append("## AI 深度评估\n")
        lines.append(f"- 来源: `{llm['source']}`")
        rd = llm.get("research_design", {})
        cr = llm.get("conclusion_reliability", {})
        lines.append(f"- 研究设计: {rd.get('score')}/100 — {rd.get('detail')}")
        lines.append(f"- 结论可靠性: {cr.get('score')}/100 — {cr.get('detail')}")
        cv = llm.get("citation_value", {})
        lines.append(f"- 引用意图: {cv.get('intent')} — {cv.get('detail')}")
        ct = llm.get("controversy", {})
        lines.append(f"- 争议信号: {'检测到' if ct.get('detected') else '未检测到'} {ct.get('detail')}")
        lines.append("")

    if report["source_trace"]:
        lines.append("## 数据来源留痕\n")
        lines.append("| 源 | 状态 | 错误 | 提供字段 |")
        lines.append("| --- | --- | --- | --- |")
        for entry in report["source_trace"]:
            fields = ",".join(entry.get("fields", [])[:6])
            lines.append(
                f"| {entry.get('source')} | {'✓' if entry.get('ok') else '✗'} | {entry.get('error') or '-'} | {fields} |"
            )
        lines.append("")

    lines.append("---\n*本报告由文献评估网站自动生成*")
    return "\n".join(lines)
