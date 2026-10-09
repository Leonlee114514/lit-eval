import { Check, X } from "lucide-react";

interface Props {
  structure: Record<string, unknown> | null;
}

const ITEMS = [
  { key: "has_problem", label: "研究问题" },
  { key: "has_method", label: "研究方法" },
  { key: "has_result", label: "研究结果" },
  { key: "has_conclusion", label: "结论" },
];

export default function StructureChecklist({ structure }: Props) {
  if (!structure) return null;
  const score = (structure.score as number) ?? 0;
  const sampleSize = structure.sample_size as number | null;
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <strong>摘要结构完整性</strong>
        <span className="muted">{score}/100</span>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {ITEMS.map(item => {
          const ok = Boolean(structure[item.key]);
          const Icon = ok ? Check : X;
          return (
            <span key={item.key} className={`badge badge-with-icon ${ok ? "badge-deep_read" : "badge-reject"}`}>
              <Icon size={12} strokeWidth={2} aria-hidden="true" /> {item.label}
            </span>
          );
        })}
        {sampleSize && <span className="badge badge-pending">样本量 n={sampleSize}</span>}
      </div>
      {structure.evidence_snippet ? (
        <div className="small muted" style={{ marginTop: 10 }}>
          证据片段：{String(structure.evidence_snippet)}
        </div>
      ) : null}
    </div>
  );
}
