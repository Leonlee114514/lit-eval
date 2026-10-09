// 结果页的筛选 / 排序 / 计数：从 ResultsPage 抽出来的纯函数。
//
// 抽出来的唯一理由是**可验证**：这些规则以前内联在组件的 useMemo 里，
// 没法单测 —— 而它们的错法是静默的（"缺分数的论文排在最前面"、
// "筛了 tier 却把没评估的也算进来"），只有人盯着表格才会发现。

import type { Paper } from "../api/types";

export type SortKey = "composite" | "title" | "journal" | "citations" | "journal_percentile" | "tier";
export type SortDir = "asc" | "desc";
export type SortState = { key: SortKey; dir: SortDir };

/** tier → 排序权重；未评估 = 0，永远排在"不推荐"之后 */
export function tierRank(t: string | null | undefined): number {
  return t === "high_priority" ? 4
    : t === "recommended" ? 3
    : t === "conditional" ? 2
    : t === "not_recommended" ? 1
    : 0;
}

/** 按筛选项过滤：all / pending（尚未评估）/ 具体 tier */
export function filterPapers(papers: Paper[], filter: string): Paper[] {
  if (filter === "all") return papers;
  return papers.filter(p => {
    const t = p.evaluation?.tier;
    if (filter === "pending") return !t;
    return t === filter;
  });
}

/** 取某列的排序键值：数值列缺失记 -1（比 0 更靠后），文本列缺失退化到 doi */
export function sortValue(p: Paper, key: SortKey): number | string {
  switch (key) {
    case "composite": return p.evaluation?.composite_score ?? -1;
    case "title": return p.title ?? p.doi ?? "";
    case "journal": return p.journal ?? "";
    case "citations": return p.cited_by_count ?? -1;
    case "journal_percentile": return p.journal_percentile ?? -1;
    case "tier": return tierRank(p.evaluation?.tier);
  }
}

/** 排序（返回新数组，不改入参）。文本按 zh-Hans-CN 比较，数值做差。 */
export function sortPapers(papers: Paper[], key: SortKey, dir: SortDir): Paper[] {
  return papers.slice().sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    const cmp = typeof av === "string" || typeof bv === "string"
      ? String(av).localeCompare(String(bv), "zh-Hans-CN")
      : av - bv;
    return dir === "asc" ? cmp : -cmp;
  });
}

/** 新点一列的默认方向：文本列升序（A→Z），数值与档位列降序（大到小） */
export function defaultDir(key: SortKey): SortDir {
  return key === "title" || key === "journal" ? "asc" : "desc";
}

/** 点列头：同列翻转方向，换列用该列的默认方向 */
export function toggleSort(current: SortState, key: SortKey): SortState {
  return current.key === key
    ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
    : { key, dir: defaultDir(key) };
}

/** 选中里还没有全文、真正需要下载的篇数 */
export function countDownloadable(papers: Paper[], selected: Set<string>): number {
  return [...selected].filter(id => !papers.find(p => p.id === id)?.has_fulltext).length;
}
