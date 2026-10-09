import { BadgeCheck, Ban, CircleAlert, CircleHelp, TriangleAlert } from "lucide-react";
import type { EvaluationDetail } from "../api/types";

const STYLES: Record<string, { label: string; cls: string; Icon: typeof BadgeCheck; border: string; bg: string }> = {
  deep_read: { label: "值得精读 / 引用", cls: "badge-deep_read", Icon: BadgeCheck, border: "var(--green)", bg: "var(--panel-ok)" },
  background_only: { label: "仅作背景引用", cls: "badge-background_only", Icon: CircleAlert, border: "var(--yellow)", bg: "var(--panel-warn)" },
  reject: { label: "不建议引用", cls: "badge-reject", Icon: Ban, border: "var(--red)", bg: "var(--panel-danger)" },
};

const TIER_STYLES: Record<string, { label: string; cls: string }> = {
  high_priority: { label: "优先引用", cls: "badge-deep_read" },
  recommended: { label: "可选引用", cls: "badge-deep_read" },
  conditional: { label: "谨慎引用", cls: "badge-background_only" },
  not_recommended: { label: "不推荐", cls: "badge-reject" },
};

const EXPECTED_TIER: Record<string, string[]> = {
  deep_read: ["high_priority", "recommended"],
  background_only: ["recommended", "conditional"],
  reject: ["not_recommended"],
};

function tierDecisionConflict(decision: string | null, tier: string | null): boolean {
  if (!decision || !tier) return false;
  const allowed = EXPECTED_TIER[decision];
  return allowed ? !allowed.includes(tier) : false;
}

export default function DecisionBanner({ evaluation }: { evaluation: EvaluationDetail | null }) {
  if (!evaluation?.decision) {
    return <div className="card"><span className="badge badge-pending">尚未评估</span></div>;
  }
  const meta = STYLES[evaluation.decision] ?? { label: evaluation.decision, cls: "badge-pending", Icon: CircleHelp, border: "var(--muted)", bg: "var(--menu-hover)" };
  const tierMeta = evaluation.tier ? TIER_STYLES[evaluation.tier] : null;
  const conflict = tierDecisionConflict(evaluation.decision, evaluation.tier);
  const Icon = meta.Icon;
  return (
    <div className="card decision-banner" style={{ borderLeft: `4px solid ${meta.border}`, background: meta.bg }}>
      <div className="decision-banner-body">
        <div className="decision-icon"><Icon size={26} strokeWidth={1.7} aria-hidden="true" /></div>
        <div>
          <div className="decision-title">
            推荐档位：
            {tierMeta ? (
              <span className={`badge ${tierMeta.cls}`} style={{ fontSize: 14 }}>{tierMeta.label}</span>
            ) : (
              <span className="badge badge-pending">—</span>
            )}
            <span className="muted" style={{ marginLeft: 8 }}>综合质量分 {evaluation.composite_score ?? "—"}/100</span>
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>
            四步决策：<span className={`badge ${meta.cls}`}>{meta.label}</span>（{evaluation.decision}）
          </div>
          {conflict && (
            <div className="small conflict-note">
              <TriangleAlert size={14} strokeWidth={1.8} aria-hidden="true" /> 推荐档位与四步决策不一致：规则门已通过但综合分档位偏低，或反之。请复核缺失的元数据后重新评估。
            </div>
          )}
          <ul className="decision-reasons">
            {evaluation.decision_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
