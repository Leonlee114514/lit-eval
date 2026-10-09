import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useProjectStore } from "../store/useProjectStore";
import { useSearchStore } from "../store/useSearchStore";
import type { ImportItem } from "../utils/importParsers";
import type { SearchResult } from "../api/types";

const WORK_TYPES = [
  { value: "", label: "全部类型" },
  { value: "article", label: "研究论文" },
  { value: "review", label: "综述" },
  { value: "preprint", label: "预印本" },
  { value: "book-chapter", label: "书籍章节" },
];

// 搜索池 = OpenAlex 单页上限（服务端拉满后按相关性重排，池内本地分页）；展示每页条数。
const SEARCH_POOL_SIZE = 200;
const PAGE_SIZE = 50;

// 结果池排序方式（客户端本地重排，切换无需重新请求 OpenAlex）
type SearchSortKey = "relevance" | "citations" | "year";
const SORT_LABELS: Record<SearchSortKey, string> = {
  relevance: "相关度",
  citations: "被引量",
  year: "年份",
};

/**
 * 主题搜索面板：关键词 → OpenAlex 候选 → 池内排序/分页 → 勾选导入。
 *
 * ``initialQuery`` 是父组件在切到本面板时传进来的当前项目研究主题（原实现在 switchMode 里预填）。
 * 父组件用 ``key="search"`` 挂载，因此每次进入都是一次干净重建，不会残留上次的勾选与结果。
 */
export default function SearchPanel({ initialQuery = "" }: { initialQuery?: string }) {
  const { projectId } = useProjectStore();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { expandSynonyms, toggleExpandSynonyms } = useSearchStore();

  const [query, setQuery] = useState(initialQuery);
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [workType, setWorkType] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchMsg, setSearchMsg] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [searchPlan, setSearchPlan] = useState<
    { variants: string[]; mode: string; note: string } | null
  >(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState<SearchSortKey>("relevance");
  const [importing, setImporting] = useState(false);

  const doSearch = async (expandOverride?: boolean) => {
    if (!projectId) { setSearchMsg("请先在「项目」页选择或新建项目"); return; }
    const q = query.trim();
    if (!q) { setSearchMsg("请输入搜索关键词/研究主题"); return; }
    const expand = expandOverride ?? expandSynonyms;
    setSearching(true);
    setSearchMsg(null);
    try {
      const res = await api.searchWorks(q, {
        project_id: projectId,
        year_from: yearFrom ? Number(yearFrom) : undefined,
        year_to: yearTo ? Number(yearTo) : undefined,
        work_type: workType || undefined,
        per_page: SEARCH_POOL_SIZE,
        query_mode: expand ? "expanded" : "raw",
      });
      setResults(res.results);
      setTotal(res.total);
      setSelected(new Set());
      setPage(1);
      setSearchPlan(
        res.query_used
          ? { variants: res.query_used, mode: res.query_mode ?? "raw", note: res.query_note ?? "" }
          : null,
      );
      const est = res.total_estimated ? "（多段并集，命中数为各段之和、会重复计数）" : "";
      setSearchMsg(`搜索到 ${res.total} 篇${est}（展示前 ${res.results.length} 篇，每页 ${PAGE_SIZE} 条，可按相关度/被引量/年份排序）`);
    } catch (e: any) {
      setSearchMsg(`搜索失败：${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setSearching(false);
    }
  };

  const toggleSelect = (key: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleAll = () => {
    const selectable = results.filter(r => !r.already_imported);
    setSelected(prev => (prev.size >= selectable.length && selectable.length > 0
      ? new Set()
      : new Set(selectable.map(r => r.doi ?? r.title ?? "").filter(Boolean))));
  };

  const importSelected = async () => {
    if (!projectId) { setSearchMsg("请先在「项目」页选择或新建项目"); return; }
    const items: ImportItem[] = results
      .filter(r => selected.has(r.doi ?? r.title ?? ""))
      .map(r => (r.doi ? { doi: r.doi } : { title: r.title ?? "" }));
    if (!items.length) { setSearchMsg("请先勾选要导入的论文"); return; }
    setImporting(true);
    setSearchMsg(null);
    try {
      setSearchMsg(`解析到 ${items.length} 篇，开始导入…`);
      const res = await api.importPapers(projectId, items);
      setSearchMsg(`导入完成：${res.papers.length} 篇，跳过 ${res.skipped.length} 条`);
      qc.invalidateQueries({ queryKey: ["papers", projectId] });
      navigate("/results");
    } catch (e: any) {
      setSearchMsg(`导入失败：${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setImporting(false);
    }
  };

  const selectableCount = useMemo(() => results.filter(r => !r.already_imported).length, [results]);

  // 池内排序（客户端本地重排）→ 分页。缺值排末尾：被引/年份缺失按 -1，相关性缺失按 0。
  const sortedResults = useMemo(() => {
    const arr = [...results];
    if (sortBy === "citations") {
      arr.sort((a, b) => (b.cited_by_count ?? -1) - (a.cited_by_count ?? -1));
    } else if (sortBy === "year") {
      arr.sort((a, b) => (b.publication_year ?? -1) - (a.publication_year ?? -1));
    } else {
      arr.sort((a, b) => (b.relevance_score ?? 0) - (a.relevance_score ?? 0));
    }
    return arr;
  }, [results, sortBy]);

  const pageCount = Math.max(1, Math.ceil(sortedResults.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const visible = sortedResults.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);

  return (
    <div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <label className="small">搜索关键词 / 研究主题</label>
          <input
            style={{ width: "100%", marginTop: 6 }}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="如 waterborne polyurethane crosslinking"
            onKeyDown={e => { if (e.key === "Enter") doSearch(); }}
          />
        </div>
        <div style={{ width: 90 }}>
          <label className="small">起始年份</label>
          <input
            style={{ width: "100%", marginTop: 6 }}
            value={yearFrom}
            onChange={e => setYearFrom(e.target.value)}
            placeholder="如 2015"
            inputMode="numeric"
          />
        </div>
        <div style={{ width: 90 }}>
          <label className="small">截止年份</label>
          <input
            style={{ width: "100%", marginTop: 6 }}
            value={yearTo}
            onChange={e => setYearTo(e.target.value)}
            placeholder="如 2025"
            inputMode="numeric"
          />
        </div>
        <div style={{ width: 130 }}>
          <label className="small">文献类型</label>
          <select
            style={{ width: "100%", marginTop: 6 }}
            value={workType}
            onChange={e => setWorkType(e.target.value)}
          >
            {WORK_TYPES.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
          </select>
        </div>
        <button onClick={() => doSearch()} disabled={searching || !query.trim()}>
          {searching ? "搜索中…" : "搜索"}
        </button>
      </div>

      <label
        className="small"
        style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8, cursor: "pointer" }}
      >
        <input
          type="checkbox"
          checked={expandSynonyms}
          onChange={() => {
            toggleExpandSynonyms();
            // 已有结果时立刻按新开关重搜，方便直接对比两种口径
            if (searchPlan) void doSearch(!expandSynonyms);
          }}
        />
        扩展同义词搜索
        <span className="muted">
          （搜 waterborne 时连 aqueous / water-based 一起搜，命中更多但可能更杂）
        </span>
      </label>
      <p className="small muted" style={{ marginBottom: 8 }}>
        通过 OpenAlex 免费学术 API 搜索候选文献；带项目后按主题相关性重排，重复文献自动标记。
      </p>

      {searchMsg && <div className="muted" style={{ marginBottom: 8 }}>{searchMsg}</div>}

      {searchPlan && (
        <div className="small muted" style={{ marginBottom: 8 }}>
          实际检索式（{searchPlan.mode}
          {searchPlan.variants.length > 1 ? `，${searchPlan.variants.length} 段并集` : ""}）：
          <code style={{ marginLeft: 6 }}>{searchPlan.variants.join("  ∪  ")}</code>
          {searchPlan.note && <span>　— {searchPlan.note}</span>}
        </div>
      )}

      {results.length > 0 && (
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8, marginTop: 8 }}>
          <label className="small">排序</label>
          <select
            value={sortBy}
            onChange={e => { setSortBy(e.target.value as SearchSortKey); setPage(1); }}
          >
            {(Object.keys(SORT_LABELS) as SearchSortKey[]).map(k => (
              <option key={k} value={k}>{SORT_LABELS[k]}</option>
            ))}
          </select>
        </div>
      )}

      {results.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 4 }}>
          <table>
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input
                    type="checkbox"
                    checked={selected.size > 0 && selected.size === selectableCount}
                    ref={el => { if (el) el.indeterminate = selected.size > 0 && selected.size < selectableCount; }}
                    onChange={toggleAll}
                  />
                </th>
                <th>标题</th><th>期刊 / 年份</th><th>被引</th><th>相关性</th><th>状态</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(r => {
                const key = r.doi ?? r.title ?? "";
                const checked = selected.has(key);
                return (
                  <tr key={key || r.openalex_work_id} className={r.already_imported ? "" : "clickable"}
                      onClick={r.already_imported ? undefined : () => toggleSelect(key)}>
                    <td onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={r.already_imported}
                        onChange={() => toggleSelect(key)}
                      />
                    </td>
                    <td style={{ maxWidth: 420 }}>{r.title ?? r.doi}</td>
                    <td className="muted">{r.journal ?? "—"} / {r.publication_year ?? "—"}</td>
                    <td>{r.cited_by_count ?? "—"}</td>
                    <td>
                      {r.relevance_score != null
                        ? r.relevance_method === "no_abstract"
                          ? <span className="muted" title="无摘要且无标题，未做相关性测量">无摘要</span>
                          : r.relevance_method === "no_topic"
                            ? <span className="muted" title="项目未设置研究主题/关键词">无主题</span>
                            : <>
                                {(r.relevance_score * 100).toFixed(0)}%
                                {r.relevance_method?.endsWith("_title") && (
                                  <span className="muted" title="OpenAlex 无摘要，按标题语义评分">（无摘要）</span>
                                )}
                              </>
                        : "—"}
                    </td>
                    <td>
                      {r.already_imported
                        ? <span className="badge badge-pending">已导入</span>
                        : <span className="muted">{r.doi ? "可导入" : "按标题导入"}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {pageCount > 1 && (
        <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
          <button className="secondary" onClick={() => setPage(curPage - 1)} disabled={curPage <= 1}>
            上一页
          </button>
          <span className="muted">
            第 {curPage} / {pageCount} 页 · 每页 {PAGE_SIZE} 条 · 展示{" "}
            {(curPage - 1) * PAGE_SIZE + 1}–{Math.min(curPage * PAGE_SIZE, results.length)}
          </span>
          <button className="secondary" onClick={() => setPage(curPage + 1)} disabled={curPage >= pageCount}>
            下一页
          </button>
        </div>
      )}

      {results.length > 0 && (
        <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center" }}>
          <button onClick={importSelected} disabled={importing || selected.size === 0}>
            {importing ? "导入中…" : `导入选中（${selected.size} 篇）`}
          </button>
          <span className="muted">总命中 {total} 篇，已导入 {results.length - selectableCount} 篇重复</span>
        </div>
      )}
    </div>
  );
}
