import { useEffect, useRef, useState } from "react";
import { ArrowLeft, FileDown, FileUp, MoreHorizontal, OctagonAlert, PenLine, RefreshCw, TriangleAlert } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import RadarChart from "../components/charts/RadarChart";
import ScoreGauge from "../components/charts/ScoreGauge";
import CitationTree from "../components/charts/CitationTree";
import CitationNetwork from "../components/charts/CitationNetwork";
import MetricCard from "../components/MetricCard";
import DecisionBanner from "../components/DecisionBanner";
import StructureChecklist from "../components/StructureChecklist";
import FigureQuality from "../components/FigureQuality";
import LLMAnalysisPanel from "../components/LLMAnalysisPanel";
import CitationFormats from "../components/CitationFormats";
import ManualFillModal from "../components/ManualFillModal";
import PaperStatusTag from "../components/PaperStatusTag";
import { usePageTitle } from "../hooks/usePageTitle";
import type { EvaluationDetail } from "../api/types";

const METHOD_LABELS: Record<string, string> = {
  coverage: "主题词覆盖率",
  sbert: "语义模型 (SBERT)",
  fastembed: "多语言语义向量 (fastembed)",
  crosslingual: "跨语中性（脚本不一致）",
  no_abstract: "无摘要（中性）",
  no_topic: "无主题/关键词（中性）",
};

export default function PaperDetailPage() {
  const { paperId = "" } = useParams();
  const qc = useQueryClient();
  const [showFill, setShowFill] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);

  // 点击下拉外部自动收起「⋯ 更多」
  useEffect(() => {
    if (!showMore) return;
    const onDown = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setShowMore(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [showMore]);

  const { data: paper, isLoading } = useQuery({
    queryKey: ["paper", paperId],
    queryFn: () => api.getPaper(paperId),
  });

  const { data: evaluation, isLoading: evLoading } = useQuery({
    queryKey: ["evaluation", paperId],
    queryFn: () => api.getEvaluation(paperId),
    enabled: !!paperId,
  });

  const evalMut = useMutation({
    mutationFn: () => api.evaluatePaper(paperId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evaluation", paperId] });
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
      qc.invalidateQueries({ queryKey: ["papers"] });
    },
  });

  const exportMd = () => api.exportMarkdown(paperId);

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      await api.uploadFulltext(paperId, file);
      qc.invalidateQueries({ queryKey: ["paper", paperId] });
      // 上传后自动重新评估，让全文参与内容评分
      await api.evaluatePaper(paperId);
      qc.invalidateQueries({ queryKey: ["evaluation", paperId] });
      qc.invalidateQueries({ queryKey: ["papers"] });
    } finally {
      setUploading(false);
    }
  };

  usePageTitle(paper ? `${paper.title} · 文献评估` : "论文详情 · 文献评估");

  if (isLoading) {
    return (
      <div aria-busy="true">
        <div className="skeleton" style={{ height: 26, width: 320, marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 14, width: 480, marginBottom: 20 }} />
        <div className="grid grid-2">
          <div className="skeleton" style={{ height: 220 }} />
          <div className="skeleton" style={{ height: 220 }} />
        </div>
      </div>
    );
  }
  if (!paper) return <div className="card">论文不存在</div>;

  // getPaper 返回的 evaluation 字段实际为完整详情（类型上按简况声明，此处统一为详情）
  const ev = (evaluation ?? paper.evaluation ?? null) as EvaluationDetail | null;

  return (
    <div className="page-enter">
      <div className="toolbar">
        <Link to="/results"><ArrowLeft size={15} strokeWidth={1.8} aria-hidden="true" /> 返回结果</Link>
        <span className="muted">{paper.doi && <code>{paper.doi}</code>}</span>
        <PaperStatusTag status={paper.status} />
        <button className="secondary" onClick={() => evalMut.mutate()} disabled={evalMut.isPending}>
          <RefreshCw size={15} strokeWidth={1.8} aria-hidden="true" /> {evalMut.isPending ? "评估中…" : "重新评估"}
        </button>
        <button
          className="secondary"
          disabled={uploading}
          onClick={() => fileRef.current?.click()}
          title="解决源站无摘要导致内容分=0：上传 PDF 后自动用全文重新评估"
        >
          <FileUp size={15} strokeWidth={1.8} aria-hidden="true" /> {uploading ? "上传中…" : "上传 PDF 全文"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf"
          style={{ display: "none" }}
          onChange={e => {
            const f = e.target.files?.[0];
            if (f) handleUpload(f);
            e.target.value = "";
          }}
        />
        {ev?.composite_score != null && <CitationFormats paperId={paperId} />}
        {((paper.missing_fields?.length ?? 0) > 0 || ev?.composite_score != null) && (
          <div className="more-menu" ref={moreRef}>
            <button
              className="secondary"
              onClick={() => setShowMore(v => !v)}
              aria-haspopup="true"
              aria-expanded={showMore}
            >
              <MoreHorizontal size={16} strokeWidth={1.8} aria-hidden="true" /> 更多
            </button>
            {showMore && (
              <div className="menu-panel">
                <button onClick={() => { setShowMore(false); setShowFill(true); }}>
                  <PenLine size={14} strokeWidth={1.7} aria-hidden="true" /> 人工补全 / 修正字段
                </button>
                {ev?.composite_score != null && (
                  <button onClick={() => { setShowMore(false); exportMd(); }}>
                    <FileDown size={14} strokeWidth={1.7} aria-hidden="true" /> 导出 Markdown 报告
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <h2 className="page-title paper-title">{paper.title}</h2>
      <div className="paper-meta">
        <span>{paper.authors?.map(a => a.name).join(", ") || "作者未知"}</span>
        <span>{paper.journal || "期刊未知"}</span>
        <span>{paper.publication_year || "年份未知"}</span>
        {paper.field_of_study && <span>{paper.field_of_study}</span>}
        {paper.doi && <code>{paper.doi}</code>}
      </div>

      <section className="detail-stage">
        {paper.is_retracted && (
          <div className="card" style={{ borderLeft: "4px solid var(--red)", background: "var(--panel-danger)" }}>
            <b><OctagonAlert size={16} strokeWidth={1.8} style={{ verticalAlign: "-3px", marginRight: 6 }} aria-hidden="true" />撤稿标记</b>
            <span className="muted" style={{ marginLeft: 8 }}>
              该文献已被自动检测为撤稿（OpenAlex / Crossref 免费信号）。建议不引用，或引用时务必注明撤稿状态。
            </span>
          </div>
        )}

        <DecisionBanner evaluation={ev} />

        {(paper.missing_fields?.length > 0 || ev?.warnings?.length) ? (
          <div className="card" style={{ borderLeft: "4px solid var(--yellow)", background: "var(--panel-warn)" }}>
            <b><TriangleAlert size={16} strokeWidth={1.8} style={{ verticalAlign: "-3px", marginRight: 6 }} aria-hidden="true" />需要注意</b>
            {paper.missing_fields?.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <span>缺失字段：{paper.missing_fields.join("、")}</span>
                <span className="small muted" style={{ marginLeft: 8 }}>
                  可在详情页人工补全或点「重新评估」。
                </span>
              </div>
            )}
            {ev?.warnings?.length ? (
              <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                {ev.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            ) : null}
          </div>
        ) : null}

        <div className="detail-stage-grid">
          <div className="card stage-radar">
            <div className="stage-label">五维画像</div>
            <RadarChart scores={ev?.radar_scores ?? {}} />
          </div>
          <div className="card stage-gauge">
            <div className="stage-label">综合质量分</div>
            <ScoreGauge score={ev?.composite_score ?? null} />
            {ev?.confidence != null && (
              <div className="small muted" style={{ textAlign: "center" }}>元数据置信度 {Math.round(ev.confidence * 100)}%</div>
            )}
          </div>
          <div className="metric-strip">
            <MetricCard label="影响因子 (IF)" value={paper.impact_factor} basis={paper.jcr_quartile ? `JCR ${paper.jcr_quartile}` : undefined} badge={paper.jcr_quartile ?? undefined} />
            <MetricCard label="中科院分区" value={paper.cas_zone ? `${paper.cas_zone} 区` : null} />
            <MetricCard label="被引总数" value={paper.cited_by_count?.toLocaleString() ?? null} basis={paper.citation_percentile != null ? `同年百分位 ${paper.citation_percentile}` : undefined} />
            <MetricCard label="期刊百分位" value={paper.journal_percentile != null ? `${paper.journal_percentile}` : null} basis="学科内 0-100" />
          </div>
        </div>
      </section>

      <div className="grid grid-2" style={{ marginBottom: 16, alignItems: "start" }}>
        {/* 左列：结构 / 图表 / 摘要（内容结构维度） */}
        <div>
          <StructureChecklist structure={ev?.abstract_structure ?? null} />
          <FigureQuality figure={ev?.figure_analysis ?? null} />
          <div className="card">
            <strong>摘要</strong>
            <div className="small" style={{ marginTop: 8, color: "var(--text-soft)", whiteSpace: "pre-wrap" }}>
              {paper.abstract || "无摘要"}
            </div>
          </div>
        </div>
        {/* 右列：LLM 深度分析 / 可复现性 / 相关性（质量结论维度），与左列 3 卡对等 */}
        <div>
          <LLMAnalysisPanel llm={ev?.llm_assessment ?? null} />
          {ev?.reproducibility && (
            <div className="card">
              <strong>可重复性</strong>
              <div style={{ fontSize: 22, fontWeight: 600, marginTop: 6 }}>{ev.reproducibility.score}/100</div>
              <div className="small muted" style={{ marginTop: 6 }}>{ev.reproducibility.basis}</div>
            </div>
          )}
          {ev?.relevance && (
            <div className="card">
              <strong>与你的研究主题相关性</strong>
              <div style={{ fontSize: 22, fontWeight: 600, marginTop: 6 }}>
                {Math.round((ev.relevance.score ?? 0) * 100)}/100
              </div>
              <div className="small muted" style={{ marginTop: 6 }}>
                方法：{METHOD_LABELS[ev.relevance.method] ?? ev.relevance.method} · 主题：{ev.relevance.topic || "—"}
                {ev.relevance.basis ? ` · ${ev.relevance.basis}` : ""}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <strong>引用脉络</strong>
        <div className="small muted" style={{ marginBottom: 8 }}>
          左列：引用本文的论文 · 中间：本文 · 右列：本文引用的文献（节点大小按被引量，可缩放/平移）。
        </div>
        <CitationTree paperId={paperId} />
      </div>

      <div className="card">
        <strong>GNN 引用网络</strong>
        <div className="small muted" style={{ marginBottom: 8 }}>
          PageRank 中心性 · 社区发现 · 文献耦合 · 教科书式引用识别（蓝=本文，绿=参考文献，黄=施引文献；可拖拽/缩放）。
        </div>
        <CitationNetwork paperId={paperId} />
      </div>

      {showFill && (
        <ManualFillModal
          paper={paper}
          onClose={() => setShowFill(false)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["paper", paperId] });
            qc.invalidateQueries({ queryKey: ["evaluation", paperId] });
          }}
        />
      )}
    </div>
  );
}
