const LABELS: Record<string, string> = {
  queued: "排队中",
  fetching: "抓取中",
  metadata_ok: "元数据就绪",
  partial: "部分缺失",
  text_ready: "文本就绪",
  evaluating: "评估中",
  done: "已评估",
  failed: "失败",
};

export default function PaperStatusTag({ status }: { status: string }) {
  const cls =
    status === "done" || status === "metadata_ok" || status === "text_ready"
      ? "badge-deep_read"
      : status === "partial"
        ? "badge-background_only"
        : status === "failed"
          ? "badge-reject"
          : "badge-pending";
  return <span className={`badge ${cls}`}>{LABELS[status] ?? status}</span>;
}
