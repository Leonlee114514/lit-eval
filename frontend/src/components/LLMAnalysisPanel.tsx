import { TriangleAlert } from "lucide-react";
import type { LLMAssessment } from "../api/types";

const INTENT_LABEL: Record<string, string> = {
  background: "背景综述",
  method: "方法借鉴",
  compare: "结果/数据对比",
  theory: "理论/概念支持",
};

export default function LLMAnalysisPanel({ llm }: { llm: LLMAssessment | null }) {
  if (!llm) return null;
  const degraded = llm.source === "rule_based";
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <strong>AI 深度评估</strong>
        {degraded && (
          <span
            className="badge badge-background_only"
            title="该条评估未走 LLM：可能是历史评估（旧规则档）或当时 LLM 调用失败降级。在结果页勾选后点「LLM 深评选中」会用当前模型重算。"
          >
            规则化评估（未跑 LLM 深评）
          </span>
        )}
      </div>
      <div className="grid grid-2">
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="muted">研究设计</div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>
            {llm.research_design.score}<span className="small muted">/100</span>
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>{llm.research_design.detail}</div>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="muted">结论可靠性</div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>
            {llm.conclusion_reliability.score}<span className="small muted">/100</span>
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>{llm.conclusion_reliability.detail}</div>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="muted">内容质量</div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>
            {llm.content_quality?.score ?? "—"}<span className="small muted">/100</span>
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>{llm.content_quality?.detail ?? "—"}</div>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="muted">引用意图</div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>
            {INTENT_LABEL[llm.citation_value.intent] ?? llm.citation_value.intent}
            {llm.citation_value.confidence != null && (
              <span className="small muted" style={{ marginLeft: 8 }}>
                置信度 {Math.round(llm.citation_value.confidence)}%
              </span>
            )}
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>{llm.citation_value.detail}</div>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="muted">争议信号</div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>
            {llm.controversy.detected ? (
  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--danger-fg)" }}>
    <TriangleAlert size={15} strokeWidth={1.8} aria-hidden="true" /> 检测到
  </span>
) : "未检测到"}
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>{llm.controversy.detail || "—"}</div>
        </div>
      </div>
    </div>
  );
}
