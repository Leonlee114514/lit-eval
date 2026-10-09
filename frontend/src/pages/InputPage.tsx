import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useProjectStore } from "../store/useProjectStore";
import { useSearchStore } from "../store/useSearchStore";
import ProjectSelector from "../components/ProjectSelector";
import { usePageTitle } from "../hooks/usePageTitle";
import { parsePlain, parseCsv, parseBibtex, formatImportCount, type ImportItem } from "../utils/importParsers";
import type { SearchResult } from "../api/types";

type ImportMode = "plain" | "csv" | "bibtex" | "pmid" | "search";

const MODES: Array<{ key: ImportMode; label: string }> = [
  { key: "plain", label: "DOI / 标题" },
  { key: "csv", label: "CSV" },
  { key: "bibtex", label: "BibTeX" },
  { key: "pmid", label: "PMID" },
  { key: "search", label: "主题搜索" },
];

const WORK_TYPES = [
  { value: "", label: "全部类型" },
  { value: "article", label: "研究论文" },
  { value: "review", label: "综述" },
  { value: "preprint", label: "预印本" },
  { value: "book-chapter", label: "书籍章节" },
];

// 搜索池 = OpenAlex 单页上限（服务端拉满后按相关性重排，池内本地分页）；
// 展示每页条数。
const SEARCH_POOL_SIZE = 200;
const PAGE_SIZE = 50;

// 结果池排序方式（客户端本地重排，切换无需重新请求 OpenAlex）
type SearchSortKey = "relevance" | "citations" | "year";
const SORT_LABELS: Record<SearchSortKey, string> = {
  relevance: "相关度",
  citations: "被引量",
  year: "年份",
};

export default function InputPage() {
  usePageTitle("导入文献 · 文献评估");
  const { projectId } = useProjectStore();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [mode, setMode] = useState<ImportMode>("plain");
  const [raw, setRaw] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // 主题搜索状态
  const [query, setQuery] = useState("");
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
  const { expandSynonyms, toggleExpandSynonyms } = useSearchStore();

  const { data: projects = [] } = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
  const currentProject = projects.find(p => p.id === projectId);

  const readFile = (f: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result ?? ""));
      r.onerror = () => reject(r.error);
      r.readAsText(f, "utf-8");
    });

  const resolveItems = async (): Promise<ImportItem[]> => {
    switch (mode) {
      case "plain":
        return parsePlain(raw);
      case "csv": {
        const text = file ? await readFile(file) : raw;
        return parseCsv(text);
      }
      case "bibtex": {
        const text = file ? await readFile(file) : raw;
        return parseBibtex(text);
      }
      case "pmid": {
        // PMID 直接源：后端用 PubMed 元数据入库（无 DOI 的 PMID 也能导入）
        return raw
          .split(/\r?\n/)
          .map(l => l.trim())
          .filter(l => /^\d+$/.test(l))
          .map(pmid => ({ pmid }));
      }

      case "search":
        return [];
    }
  };

  const importPapers = async () => {
    if (!projectId) { setMessage("请先在「项目」页选择或新建项目"); return; }
    setImporting(true);
    setMessage(null);
    try {
      const items = await resolveItems();
      if (!items.length) {
        setMessage("未解析出有效条目（PMID 模式：请确认每行都是 PubMed PMID）");
        return;
      }
      setMessage(`解析到 ${formatImportCount(items)}，开始导入…`);
      const res = await api.importPapers(projectId, items);
      const errText = res.errors?.length ? `；失败 ${res.errors.length} 条` : "";
      setMessage(`导入完成：${res.papers.length} 篇，跳过 ${res.skipped.length} 条${errText}`);
      qc.invalidateQueries({ queryKey: ["papers", projectId] });
      navigate("/results");
    } catch (e: any) {
      setMessage(`导入失败：${e?.response?.data?.detail ?? e.message}`);
    } finally {
      setImporting(false);
    }
  };

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

  const switchMode = (m: ImportMode) => {
    setMode(m);
    setFile(null);
    setRaw("");
    setMessage(null);
    setSearchMsg(null);
    // 进入搜索模式时预填当前项目的研究主题
    if (m === "search" && currentProject?.research_topic) {
      setQuery(currentProject.research_topic);
    }
  };

  // 语义化 tab：方向键/Home/End 切换导入方式，焦点跟随（WAI-ARIA Tabs 模式）
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const onTablistKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = MODES.map(m => m.key);
    let idx = keys.indexOf(mode);
    if (e.key === "ArrowRight") idx = (idx + 1) % keys.length;
    else if (e.key === "ArrowLeft") idx = (idx - 1 + keys.length) % keys.length;
    else if (e.key === "Home") idx = 0;
    else if (e.key === "End") idx = keys.length - 1;
    else return;
    e.preventDefault();
    switchMode(keys[idx]);
    tabRefs.current[idx]?.focus();
  };

  const pickFile = () => fileRef.current?.click();
  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setFile(f);
      setRaw("");
      setMessage(`已选择 ${f.name}`);
    }
    e.target.value = "";
  };

  const placeholderByMode: Record<ImportMode, string> = {
    plain: "10.1038/nature14539\nDeep learning (Nature 2015)\n10.1016/j.cell.2016.07.008",
    csv: "doi,title\n10.1038/nature14539,Deep learning\n10.1016/j.cell.2016.07.008,Phase transition",
    bibtex: "@article{lecun2015,\n  title = {Deep learning},\n  doi = {10.1038/nature14539},\n  journal = {Nature}\n}",
    pmid: "26017442\n28818941\n（每行一个 PMID，PubMed 直接导入）",
    search: "",
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
    <div className="page-enter">
      <h2 className="page-title">导入文献</h2>
      <div className="card">
        <div style={{ marginBottom: 12 }}>
          <label className="small">目标项目</label>
          <ProjectSelector projects={projects} />
        </div>

        <div
          role="tablist"
          aria-label="导入方式"
          onKeyDown={onTablistKeyDown}
          style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}
        >
          {MODES.map((m, i) => (
            <button
              key={m.key}
              ref={el => { tabRefs.current[i] = el; }}
              role="tab"
              id={`import-tab-${m.key}`}
              aria-selected={mode === m.key}
              aria-controls="import-tabpanel"
              tabIndex={mode === m.key ? 0 : -1}
              className={mode === m.key ? "" : "secondary"}
              onClick={() => switchMode(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div id="import-tabpanel" role="tabpanel" aria-labelledby={`import-tab-${mode}`}>
        {mode === "search" ? (
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
        ) : (
          <>
            {mode === "csv" || mode === "bibtex" ? (
              <div style={{ marginBottom: 12 }}>
                <button className="secondary" onClick={pickFile}>
                  {file ? `已选择：${file.name}` : "选择文件"}
                </button>
                <input ref={fileRef} type="file" accept={mode === "csv" ? ".csv,.txt" : ".bib,.bibtex,.txt"} style={{ display: "none" }} onChange={onFile} />
                <span className="small muted" style={{ marginLeft: 8 }}>或直接粘贴内容到下方</span>
              </div>
            ) : null}

            <label className="small">
              {mode === "plain" && "粘贴 DOI 或论文标题（每行一个，自动识别）"}
              {mode === "csv" && "粘贴 CSV（需含 doi 或 title 列，支持带引号字段）"}
              {mode === "bibtex" && "粘贴 BibTeX 条目"}
              {mode === "pmid" && "粘贴 PMID（PubMed 编号，每行一个）"}
            </label>
            <textarea
              rows={8}
              style={{ marginTop: 6, fontFamily: mode === "bibtex" ? "monospace" : undefined }}
              value={raw}
              onChange={e => setRaw(e.target.value)}
              placeholder={placeholderByMode[mode]}
              disabled={mode === "csv" || mode === "bibtex" ? !!file : false}
            />

            <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center" }}>
              <button onClick={importPapers} disabled={importing || (!raw.trim() && !file)}>
                {importing ? "导入中…" : "开始导入"}
              </button>
              {message && <span className="muted">{message}</span>}
            </div>
            <p className="small muted" style={{ marginTop: 8 }}>
              {mode === "plain" && "DOI 行走三源抓取，标题行走 OpenAlex 搜索。抓取失败的字段会自动标记，可在详情页人工补全。"}
              {mode === "pmid" && "PMID 直接通过 NCBI PubMed 元数据导入；有 DOI 时自动叠加常规三源抓取，无 DOI 的 PMID 也能入库。"}
              {(mode === "csv" || mode === "bibtex") && "解析出的 DOI/标题走常规抓取；无法解析的条目自动跳过。"}
            </p>
          </>
        )}
        </div>
      </div>
    </div>
  );
}
