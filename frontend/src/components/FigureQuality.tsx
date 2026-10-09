import type { EvaluationDetail } from "../api/types";

interface Props {
  figure: EvaluationDetail["figure_analysis"];
}

const KIND_LABEL: Record<string, string> = {
  data: "原始数据图",
  schematic: "示意图",
  unknown: "未分类",
};

export default function FigureQuality({ figure }: Props) {
  if (!figure) return null;
  const hasFigures = (figure.total_figures ?? 0) > 0;
  const score = figure.score;
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <strong>图表质量检测</strong>
        <span className="muted">{score != null ? `${score}/100` : "无原料"}</span>
      </div>
      {!hasFigures ? (
        <div className="small muted">未检测到图表题注（仅全文 PDF 可分析）</div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <span className="badge badge-deep_read">图 {figure.total_figures} 张</span>
            {figure.data_figures > 0 && (
              <span className="badge badge-deep_read">数据图 {figure.data_figures}</span>
            )}
            {figure.schematic_figures > 0 && (
              <span className="badge badge-conditional">示意图 {figure.schematic_figures}</span>
            )}
            {figure.unclassified_figures > 0 && (
              <span className="badge badge-pending">未分类 {figure.unclassified_figures}</span>
            )}
            {figure.tables > 0 && <span className="badge badge-pending">表 {figure.tables}</span>}
          </div>
          {figure.basis && (
            <div className="small muted" style={{ marginTop: 8 }}>
              {figure.basis}
            </div>
          )}
          {figure.figures?.length > 0 && (
            <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18, color: "var(--text-soft)" }}>
              {figure.figures.slice(0, 8).map(f => (
                <li key={f.number} style={{ marginBottom: 2 }}>
                  图 {f.number} · {KIND_LABEL[f.kind] ?? f.kind} — {f.caption}
                </li>
              ))}
              {figure.figures.length > 8 && (
                <li className="muted">…共 {figure.figures.length} 张</li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
