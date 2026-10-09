import { useState } from "react";
import { api } from "../api/client";
import type { Paper } from "../api/types";

interface Props {
  paper: Paper;
  onClose: () => void;
  onSaved: () => void;
}

const FIELDS: { key: string; label: string; type: "number" | "text" }[] = [
  { key: "impact_factor", label: "影响因子 (IF)", type: "number" },
  { key: "jcr_quartile", label: "JCR 分区 (Q1-Q4)", type: "text" },
  { key: "cas_zone", label: "中科院分区 (1-4)", type: "number" },
  { key: "publication_year", label: "发表年份", type: "number" },
];

export default function ManualFillModal({ paper, onClose, onSaved }: Props) {
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    const body: Record<string, unknown> = {};
    for (const f of FIELDS) {
      const raw = form[f.key]?.trim();
      if (!raw) continue;
      if (f.type === "number") {
        const n = Number(raw);
        if (!Number.isNaN(n)) body[f.key] = n;
      } else {
        body[f.key] = raw;
      }
    }
    await api.patchPaper(paper.id, body);
    setSaving(false);
    onSaved();
    onClose();
  };

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      }}
      onClick={onClose}
    >
      <div className="card" style={{ width: 420 }} onClick={e => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>人工补全 / 修正字段</h3>
        {paper.missing_fields.length > 0 && <p className="muted">缺失：{paper.missing_fields.join("、")}</p>}
        {FIELDS.map(f => (
          <div key={f.key} style={{ marginBottom: 10 }}>
            <label className="small" style={{ display: "block", marginBottom: 4 }}>{f.label}</label>
            <input
              type={f.type === "number" ? "number" : "text"}
              value={form[f.key] ?? ""}
              placeholder={String((paper as unknown as Record<string, unknown>)[f.key] ?? "")}
              onChange={e => setForm(prev => ({ ...prev, [f.key]: e.target.value }))}
            />
          </div>
        ))}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 12 }}>
          <button className="secondary" onClick={onClose}>取消</button>
          <button onClick={save} disabled={saving}>{saving ? "保存中…" : "保存"}</button>
        </div>
      </div>
    </div>
  );
}
