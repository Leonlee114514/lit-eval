import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown, Ban, CheckCircle2, CircleAlert, Clock3, FileText, FlaskConical, Layers, Network, Share2, Star } from "lucide-react";
import { api } from "../api/client";
import type { Paper, ProjectNetworkData } from "../api/types";
import { useProjectStore } from "../store/useProjectStore";
import ProjectSelector from "../components/ProjectSelector";
import PaperStatusTag from "../components/PaperStatusTag";
import ProjectNetwork from "../components/charts/ProjectNetwork";
import { usePageTitle } from "../hooks/usePageTitle";

const DECISION_LABEL: Record<string, string> = {
  deep_read: "精读",
  background_only: "背景",
  reject: "不推荐",
};

const TIER_LABEL: Record<string, { label: string; cls: string }> = {
  // 文案与推荐卡/决策横幅/筛选下拉统一（P1-5：此前表格用"优先/可选"，卡片用"优先引用/可选引用"）
  high_priority: { label: "优先引用", cls: "badge-deep_read" },
  recommended: { label: "可选引用", cls: "badge-deep_read" },
  conditional: { label: "谨慎引用", cls: "badge-background_only" },
  not_recommended: { label: "不推荐", cls: "badge-reject" },
};

export default function ResultsPage() {
  usePageTitle("评估结果 · 文献评估");
  const { projectId } = useProjectStore();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<string>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [downloadTask, setDownloadTask] = useState<{ id: string; done: boolean; msg: string } | null>(null);

  type SortKey = "composite" | "title" | "journal" | "citations" | "journal_percentile" | "tier";
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "composite", dir: "desc" });
  const toggleSort = (key: SortKey) =>
    setSort(prev =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "title" || key === "journal" ? "asc" : "desc" }
    );

  const { data: projects = [] } = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });

  const { data: papers = [], isLoading } = useQuery<Paper[]>({
    queryKey: ["papers", projectId],
    queryFn: () => api.listPapers(projectId!),
    enabled: !!projectId,
    refetchInterval: (query) => {
      // 有未完成状态时轮询
      const list = query.state.data as Paper[] | undefined;
      return list?.some(p => ["queued", "fetching", "evaluating"].includes(p.status)) ? 3000 : false;
    },
  });

  const { data: projectNetwork } = useQuery<ProjectNetworkData | null>({
    queryKey: ["projectNetwork", projectId],
    queryFn: () => api.getProjectCitationNetwork(projectId!),
    enabled: !!projectId && papers.length > 1,
    retry: false,
  });

  const evalAllMut = useMutation({
    // 批量评估全部：默认走 LLM（client 默认 llm=true），与按钮文案「LLM 评估全部」一致
    mutationFn: () => api.evaluateAll(projectId!),
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["papers", projectId] }), 2000);
    },
  });

  const evalSelMut = useMutation({
    // 选中论文 LLM 深度评估（用户主动升级档位）
    mutationFn: (ids: string[]) => api.evaluateSelected(projectId!, ids, true),
    onSuccess: () => {
      setSelected(new Set());
      setTimeout(() => qc.invalidateQueries({ queryKey: ["papers", projectId] }), 2000);
    },
  });

  const downloadMut = useMutation({
    // 批量下载选中论文的 PDF 全文（后端四通道，自动跳过已有全文）
    mutationFn: (ids: string[]) => api.downloadPdfs(projectId!, ids),
    onSuccess: (d) => {
      setSelected(new Set());
      setDownloadTask({ id: d.task_id, done: false, msg: "排队中…" });
    },
  });

  // 轮询下载任务进度，结束后刷新列表（text_ready 不在现有 refetchInterval 白名单里，必须手动刷新）
  useEffect(() => {
    if (!downloadTask || downloadTask.done) return;
    const timer = setInterval(async () => {
      try {
        const t = await api.taskStatus(downloadTask.id);
        if (t.status === "done" || t.status === "failed") {
          const results = ((t.result as { results?: Array<{ status: string }> } | undefined)?.results) ?? [];
          const ok = results.filter(x => x.status === "done").length;
          const skipped = results.filter(x => x.status === "skipped").length;
          const failed = results.filter(x => x.status === "failed").length;
          const tail = results.length ? `（成功 ${ok}${skipped ? `，跳过 ${skipped}` : ""}${failed ? `，失败 ${failed}` : ""}）` : "";
          setDownloadTask({ id: downloadTask.id, done: true, msg: `${t.message}${tail}` });
          qc.invalidateQueries({ queryKey: ["papers", projectId] });
        } else {
          setDownloadTask(prev => (prev ? { ...prev, msg: t.message || "下载中…" } : prev));
        }
      } catch {
        // 网络抖动下一轮重试
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [downloadTask, projectId, qc]);

  // 选中里还没有全文、真正需要下载的篇数
  const downloadable = useMemo(
    () => [...selected].filter(id => !papers.find(p => p.id === id)?.has_fulltext).length,
    [selected, papers],
  );

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filtered = useMemo(() => {
    if (filter === "all") return papers;
    return papers.filter(p => {
      const t = p.evaluation?.tier;
      if (filter === "pending") return !t;
      return t === filter;
    });
  }, [papers, filter]);

  const sorted = useMemo(() => {
    const tierRank = (t: string | null | undefined) =>
      t === "high_priority" ? 4 : t === "recommended" ? 3 : t === "conditional" ? 2 : t === "not_recommended" ? 1 : 0;
    const val = (p: Paper): number | string => {
      switch (sort.key) {
        case "composite": return p.evaluation?.composite_score ?? -1;
        case "title": return p.title ?? p.doi ?? "";
        case "journal": return p.journal ?? "";
        case "citations": return p.cited_by_count ?? -1;
        case "journal_percentile": return p.journal_percentile ?? -1;
        case "tier": return tierRank(p.evaluation?.tier);
      }
    };
    return filtered.slice().sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      const cmp = typeof av === "string" || typeof bv === "string"
        ? String(av).localeCompare(String(bv), "zh-Hans-CN")
        : (av as number) - (bv as number);
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [filtered, sort]);

  const sortIcon = (key: SortKey) =>
    sort.key !== key ? <ArrowUpDown size={12} strokeWidth={1.8} aria-hidden="true" /> :
    sort.dir === "asc" ? <ArrowUp size={12} strokeWidth={1.8} aria-hidden="true" /> :
    <ArrowDown size={12} strokeWidth={1.8} aria-hidden="true" />;

  const ariaSort = (key: SortKey) =>
    sort.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none";

  const currentProject = projects.find(p => p.id === projectId);
  const evaluatedCount = papers.filter(p => p.evaluation?.composite_score != null).length;
  const fulltextCount = papers.filter(p => p.has_fulltext).length;

  const onRowKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, id: string) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      navigate(`/papers/${id}`);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const rows = Array.from(e.currentTarget.closest("tbody")?.querySelectorAll("tr.clickable") ?? []);
      const idx = rows.indexOf(e.currentTarget);
      const next = e.key === "ArrowDown" ? idx + 1 : idx - 1;
      if (next >= 0 && next < rows.length) (rows[next] as HTMLTableRowElement).focus();
    }
  };

  // 推荐列表分组统计（按综合分分数段 tier：优先/可选/谨慎/不推荐）
  const groups = useMemo(() => {
    const counts = { high_priority: 0, recommended: 0, conditional: 0, not_recommended: 0, pending: 0 };
    for (const p of papers) {
      const t = p.evaluation?.tier;
      if (t === "high_priority" || t === "recommended" || t === "conditional" || t === "not_recommended") counts[t]++;
      else counts.pending++;
    }
    return counts;
  }, [papers]);

  const GROUP_META = [
    { key: "high_priority", label: "优先引用", hint: "综合分 ≥85", bg: "var(--ok-bg)", fg: "var(--ok-fg)", Icon: Star },
    { key: "recommended", label: "可选引用", hint: "综合分 60-84", bg: "var(--ok-bg)", fg: "var(--ok-fg)", Icon: CheckCircle2 },
    { key: "conditional", label: "谨慎引用", hint: "综合分 35-59", bg: "var(--warn-bg)", fg: "var(--warn-fg)", Icon: CircleAlert },
    { key: "not_recommended", label: "不推荐", hint: "综合分 <35", bg: "var(--danger-bg)", fg: "var(--danger-fg)", Icon: Ban },
    { key: "pending", label: "待评估", hint: "未完成", bg: "var(--neutral-bg)", fg: "var(--neutral-fg)", Icon: Clock3 },
  ] as const;

  return (
    <div className="page-enter">
      <div className="toolbar">
        <h2 className="page-title" style={{ marginBottom: 0 }}>评估结果</h2>
        <div style={{ width: 240 }}><ProjectSelector projects={projects} /></div>
        <select
          value={filter}
          onChange={e => setFilter(e.target.value)}
        >
          <option value="all">全部档位</option>
          <option value="high_priority">优先引用</option>
          <option value="recommended">可选引用</option>
          <option value="conditional">谨慎引用</option>
          <option value="not_recommended">不推荐</option>
          <option value="pending">待评估</option>
        </select>
        <button
          onClick={() => evalAllMut.mutate()}
          disabled={!projectId || evalAllMut.isPending || !papers.length}
          title="对全部论文运行 LLM 深度评估（内容质量分由 LLM 综合判定）"
        >
          {evalAllMut.isPending ? "LLM 评估中…" : "LLM 评估全部"}
        </button>
        <button
          className="secondary"
          onClick={() => evalSelMut.mutate([...selected])}
          disabled={!projectId || evalSelMut.isPending || selected.size === 0}
          title="对选中的论文运行 LLM 深度评估（研究设计/结论可靠性/引用价值/争议检测）"
        >
          {evalSelMut.isPending ? "深评中…" : `LLM 深评选中（${selected.size}）`}
        </button>
        <button
          className="secondary"
          onClick={() => downloadMut.mutate([...selected])}
          disabled={!projectId || downloadMut.isPending || downloadable === 0}
          title="对选中的论文批量下载 PDF 全文（Unpaywall/OpenAlex → Sci-Hub），已有全文的自动跳过"
        >
          {downloadMut.isPending ? "提交中…" : `下载 PDF（${downloadable}）`}
        </button>
        {downloadTask && (
          <span role="status" aria-live="polite" className="small muted" style={{ marginLeft: 8 }}>{downloadTask.msg}</span>
        )}
        <span role="status" aria-live="polite" className="small muted" style={{ marginLeft: 8 }}>
          {(evalAllMut.isPending || evalSelMut.isPending) && "正在运行 LLM 深度评估，完成后自动刷新…"}
        </span>
        {selected.size === 0 && papers.length > 0 && (
          <span className="small muted" style={{ marginLeft: 8 }}>
            在下方表格勾选论文行左侧的复选框（或表头全选）
          </span>
        )}
      </div>
      <details className="small muted" style={{ marginBottom: 8 }}>
        <summary style={{ cursor: "pointer" }}>操作说明</summary>
        <p style={{ margin: "6px 0 0" }}>
          「LLM 评估全部」对全部论文跑 AI 深度评估（内容质量分由 LLM 判定）；「LLM 深评选中」对勾选子集重跑；「下载 PDF」批量获取全文并自动入库（跳过已有全文的论文），评估将自动读取全文。
        </p>
      </details>

      {projectId && papers.length > 0 && !isLoading && (
        <div className="project-hero card">
          <div className="hero-main">
            <span className="hero-kicker">研究项目</span>
            <h2 className="hero-title"><FlaskConical size={19} strokeWidth={1.7} aria-hidden="true" /> {currentProject?.name || "未命名项目"}</h2>
            <div className="hero-topic">
              <span className="topic-pill"><Network size={13} strokeWidth={1.7} aria-hidden="true" /> {currentProject?.research_topic || "未设置研究主题"}</span>
              {currentProject?.keywords?.map(k => <span key={k} className="topic-pill topic-keyword">{k}</span>)}
            </div>
          </div>
          <div className="hero-stats">
            <div className="hero-stat"><FileText size={14} strokeWidth={1.7} aria-hidden="true" /><span><b>{papers.length}</b> 文献</span></div>
            <div className="hero-stat"><CheckCircle2 size={14} strokeWidth={1.7} aria-hidden="true" /><span><b>{evaluatedCount}</b> 已评估</span></div>
            <div className="hero-stat"><Share2 size={14} strokeWidth={1.7} aria-hidden="true" /><span><b>{fulltextCount}</b> 有全文</span></div>
            <div className="hero-stat"><Layers size={14} strokeWidth={1.7} aria-hidden="true" /><span><b>{projectNetwork?.stats.shared_reference_count ?? "—"}</b> 共享文献</span></div>
            <div className="hero-stat"><Network size={14} strokeWidth={1.7} aria-hidden="true" /><span><b>{projectNetwork?.stats.coupling_edge_count ?? "—"}</b> 耦合边</span></div>
          </div>
        </div>
      )}

      {!projectId ? (
        <div className="card muted">请先选择项目。</div>
      ) : isLoading ? (
        <div aria-busy="true">
          <div className="skeleton" style={{ height: 18, width: 200, marginBottom: 16 }} />
          <div className="recommend-cards" style={{ marginBottom: 16 }}>
            {[0, 1, 2, 3, 4].map(i => (
              <div key={i} className="skeleton" style={{ height: 96 }} />
            ))}
          </div>
          <div className="skeleton" style={{ height: 260 }} />
        </div>
      ) : papers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: "48px 16px" }}>
          <div className="empty-icon"><FileText size={42} strokeWidth={1.4} aria-hidden="true" /></div>
          <h3 style={{ margin: "12px 0 6px" }}>项目内暂无文献</h3>
          <p className="muted" style={{ margin: "0 0 16px" }}>
            去「导入文献」页用 DOI / 主题搜索导入第一批论文，回来后即可跑评估、看分布。
          </p>
          <button onClick={() => navigate("/input")}>去导入文献</button>
        </div>
      ) : (
        <>
          <div className="recommend-cards" style={{ marginBottom: 16 }}>
            {GROUP_META.map(g => (
              <div
                key={g.key}
                className="card"
                style={{
                  marginBottom: 0, cursor: "pointer", borderLeft: `4px solid ${g.fg}`,
                  background: filter === g.key ? "var(--accent-tint)" : undefined,
                }}
                onClick={() => setFilter(filter === g.key ? "all" : g.key)}
                title={`点击筛选「${g.hint}」`}
              >
                <div className="group-icon" style={{ color: g.fg }}><g.Icon size={20} strokeWidth={1.7} aria-hidden="true" /></div>
                <div className="group-count">{groups[g.key]}</div>
                <div style={{ fontWeight: 600 }}>{g.label}</div>
                <div className="small muted">{g.hint}</div>
              </div>
            ))}
          </div>

          <div className="card" style={{ padding: 0, overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 32 }}>
                    <input
                      type="checkbox"
                      checked={selected.size > 0 && selected.size === filtered.length}
                      ref={el => { if (el) el.indeterminate = selected.size > 0 && selected.size < filtered.length; }}
                      onChange={() => setSelected(prev => (prev.size > 0 ? new Set() : new Set(filtered.map(p => p.id))))}
                    />
                  </th>
                  <th aria-sort={ariaSort("title")}><button className="th-sort" onClick={() => toggleSort("title")}>标题 {sortIcon("title")}</button></th>
                  <th aria-sort={ariaSort("journal")}><button className="th-sort" onClick={() => toggleSort("journal")}>期刊 / 年份 {sortIcon("journal")}</button></th>
                  <th aria-sort={ariaSort("citations")}><button className="th-sort" onClick={() => toggleSort("citations")}>被引 {sortIcon("citations")}</button></th>
                  <th aria-sort={ariaSort("journal_percentile")}><button className="th-sort" onClick={() => toggleSort("journal_percentile")}>期刊百分位 {sortIcon("journal_percentile")}</button></th>
                  <th aria-sort={ariaSort("composite")}><button className="th-sort" onClick={() => toggleSort("composite")}>综合分 {sortIcon("composite")}</button></th>
                  <th aria-sort={ariaSort("tier")}><button className="th-sort" onClick={() => toggleSort("tier")}>推荐档位 {sortIcon("tier")}</button></th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(p => (
                    <tr
                      key={p.id}
                      className="clickable"
                      tabIndex={0}
                      aria-label={`查看 ${p.title ?? p.doi ?? "论文"} 详情`}
                      onClick={() => navigate(`/papers/${p.id}`)}
                      onKeyDown={e => onRowKeyDown(e, p.id)}
                    >
                      <td onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selected.has(p.id)}
                          onChange={() => toggleSelect(p.id)}
                        />
                      </td>
                      <td style={{ maxWidth: 420 }}>
                        {p.has_fulltext && <span title="已有全文 PDF" aria-label="已有全文 PDF"><FileText size={14} strokeWidth={1.7} style={{ verticalAlign: "-2px", marginRight: 4, color: "var(--muted)" }} /></span>}
                        {p.title ?? p.doi}
                      </td>
                      <td className="muted">{p.journal ?? "—"} / {p.publication_year ?? "—"}</td>
                      <td>{p.cited_by_count ?? "—"}</td>
                      <td>{p.journal_percentile != null ? p.journal_percentile : "—"}</td>
                      <td><b>{p.evaluation?.composite_score ?? "—"}</b></td>
                      <td>
                        {p.evaluation?.tier ? (
                          <span className={`badge ${TIER_LABEL[p.evaluation.tier]?.cls ?? "badge-pending"}`}>
                            {TIER_LABEL[p.evaluation.tier]?.label ?? p.evaluation.tier}
                          </span>
                        ) : "—"}
                      </td>
                      <td><PaperStatusTag status={p.status} /></td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <strong>项目 GNN 引用网络</strong>
            <div className="small muted" style={{ marginBottom: 8 }}>
              蓝点=项目论文，绿点=被至少 2 篇论文共享的参考文献；实线=引用，虚线=论文间文献耦合（可拖拽/缩放）。
            </div>
            <ProjectNetwork projectId={projectId!} />
          </div>
        </>
      )}
    </div>
  );
}
