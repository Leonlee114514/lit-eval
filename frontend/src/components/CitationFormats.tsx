import { useState } from "react";
import { Check, Quote } from "lucide-react";
import { api } from "../api/client";
import type { Report } from "../api/types";

const STYLES = [
  { key: "apa", label: "APA (7th)" },
  { key: "mla", label: "MLA (9th)" },
  { key: "gbt7714", label: "GB/T 7714" },
] as const;

export default function CitationFormats({ paperId }: { paperId: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.getReport(paperId);
      setReport(r);
      setOpen(true);
    } finally {
      setLoading(false);
    }
  };

  const copy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* 剪贴板不可用时忽略 */
    }
  };

  if (!open) {
    return (
      <button className="secondary" disabled={loading} onClick={load}>
        <Quote size={15} strokeWidth={1.7} aria-hidden="true" /> {loading ? "加载中…" : "查看引用格式"}
      </button>
    );
  }

  const formats = report?.citation_formats ?? {};
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <strong>标准引用格式</strong>
        <button className="secondary" onClick={() => setOpen(false)}>收起</button>
      </div>
      {STYLES.map(s => {
        const text = formats[s.key];
        return (
          <div key={s.key} style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span className="small" style={{ fontWeight: 600 }}>{s.label}</span>
              {text && (
                <button className="secondary small" onClick={() => copy(text, s.key)}>
                  {copied === s.key ? <><Check size={12} strokeWidth={2} aria-hidden="true" /> 已复制</> : "复制"}
                </button>
              )}
            </div>
            <div
              className="small"
              style={{ background: "var(--bg-soft)", padding: "8px 10px", borderRadius: 6, marginTop: 4, fontFamily: "monospace", whiteSpace: "pre-wrap" }}
            >
              {text || "字段缺失，暂无法生成"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
