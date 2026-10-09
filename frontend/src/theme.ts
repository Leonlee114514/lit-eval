// 设计 token 的唯一前端源：CSS 变量定义在 index.css :root 与 [data-theme=dark]
// 图表（ECharts 用 SVG/Canvas，读不到 CSS 变量）通过本 helper 取色，避免语义色硬编码漂移
// 注意：不缓存——暗色主题切换后 getComputedStyle 立即返回新值，图表随主题重绘
export function cssColor(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// 语义色别名（与 index.css 变量一一对应）
export const C = {
  primary: (): string => cssColor("--primary", "#2563eb"),
  green: (): string => cssColor("--green", "#16a34a"),
  yellow: (): string => cssColor("--yellow", "#ca8a04"),
  red: (): string => cssColor("--red", "#dc2626"),
  muted: (): string => cssColor("--muted", "#6b7280"),
  textSoft: (): string => cssColor("--text-soft", "#374151"),
  chartText: (): string => cssColor("--chart-text", "#4b5563"),
  line: (): string => cssColor("--chart-line", "#cbd5e1"),
  track: (): string => cssColor("--chart-track", "#e5e7eb"),
  gaugeText: (): string => cssColor("--gauge-text", "#1f2430"),
};
