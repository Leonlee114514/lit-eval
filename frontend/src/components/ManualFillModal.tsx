import { useEffect, useRef, useState } from "react";
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

const TITLE_ID = "manual-fill-title";
const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select, textarea, a[href]';

/**
 * 人工补全弹窗。
 *
 * 键盘与读屏支持是 2026-10 无障碍审计后补的（此前这个弹窗只有鼠标可用）：
 * - `role="dialog"` + `aria-modal` + `aria-labelledby`：读屏才会念"对话框：人工补全…"
 * - 打开时焦点移入弹窗、关闭后回给触发它的按钮
 * - Tab 焦点陷阱：循环在弹窗内，不会跑到背后的页面元素上
 * - Esc 关闭
 * - 锁定背景滚动
 */
export default function ManualFillModal({ paper, onClose, onSaved }: Props) {
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  // onClose 通常是父组件的内联箭头函数，每次渲染都是新引用 ——
  // 放进依赖数组会让"卸载清理"反复执行、焦点乱跳。用 ref 固定住它。
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";  // 锁定背景滚动
    dialogRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const nodes = dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      // 焦点已经跑到弹窗外 → 拉回开头
      if (!dialogRef.current?.contains(active)) {
        e.preventDefault();
        first.focus();
        return;
      }
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus?.();  // 焦点还给触发按钮
    };
  }, []);  // 只在挂载/卸载时执行一次

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
      role="presentation"
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        className="card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        tabIndex={-1}
        style={{ width: 420, outline: "none" }}
        onClick={e => e.stopPropagation()}
      >
        <h3 id={TITLE_ID} style={{ marginTop: 0 }}>人工补全 / 修正字段</h3>
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
