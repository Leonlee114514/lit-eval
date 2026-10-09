import { useEffect, useMemo, useRef, useState } from "react";
import { echarts } from "./echarts";
import { C } from "../../theme";
import { useThemeStore } from "../../store/useThemeStore";
import { api } from "../../api/client";
import type { ProjectNetworkData, ProjectNetworkNode } from "../../api/types";

interface Props {
  projectId: string;
  height?: number;
}

const ROLE_LABEL: Record<string, string> = { project_paper: "项目论文", reference: "共享参考文献" };

function nodeName(n: ProjectNetworkNode): string {
  return (n.title || n.id || "未知").slice(0, 34);
}

export default function ProjectNetwork({ projectId, height = 460 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<ProjectNetworkData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const theme = useThemeStore(s => s.theme);

  useEffect(() => {
    let disposed = false;
    setError(null);
    setData(null);
    api
      .getProjectCitationNetwork(projectId)
      .then(raw => {
        if (!disposed) setData(raw ?? null);
      })
      .catch(() => {
        if (!disposed) setError("项目 GNN 网络加载失败（需要至少 2 篇含 OpenAlex 标识且有共享参考文献的论文）");
      });
    return () => {
      disposed = true;
    };
  }, [projectId]);

  const series = useMemo(() => {
    if (!data) return null;
    const nodes = data.nodes.map(n => ({
      id: n.id,
      name: `${nodeName(n)}\n${n.role === "project_paper" ? `被引 ${n.cited_by_count}` : `共被引 ${n.co_cited_count ?? 0} · 被引 ${n.cited_by_count}`}`,
      symbolSize: n.role === "project_paper"
        ? Math.min(52, 16 + Math.log2((n.pagerank ?? 0) * 100 + 1) * 8)
        : Math.min(40, 10 + Math.log2((n.co_cited_count ?? 0) + 1) * 9),
      category: n.role === "project_paper" ? 0 : 1,
      value: Math.max(1, (n.pagerank ?? 0) * 1000),
      raw: n,
    }));
    const links = data.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      lineStyle: {
        width: e.type === "coupling" ? Math.min(5, 1 + e.weight) : 1,
        opacity: e.type === "coupling" ? 0.7 : 0.25,
        type: e.type === "coupling" ? "dashed" : "solid",
      },
      raw: e,
    }));
    return { nodes, links };
  }, [data]);

  useEffect(() => {
    if (!ref.current || !series || !data) return;
    const byId = new Map(data.nodes.map(n => [n.id, n]));
    const chart = echarts.init(ref.current);
    const categories = [
      { name: "项目论文", itemStyle: { color: C.primary() } },
      { name: "共享参考文献", itemStyle: { color: C.green() } },
    ];
    chart.setOption({
      tooltip: {
        formatter: (p: any) => {
          const n = (p.data?.id ? byId.get(p.data.id) : undefined) as ProjectNetworkNode | undefined;
          if (!n) return p.name;
          return [
            `<b>${n.title || n.id}</b>`,
            `${ROLE_LABEL[n.role] ?? n.role} · ${n.publication_year ?? "年份未知"}`,
            n.role === "reference" ? `共被引 ${n.co_cited_count ?? 0} · 总被引 ${n.cited_by_count}` : `被引 ${n.cited_by_count}`,
            `PageRank ${(n.pagerank ?? 0).toFixed(5)} · 社区 #${n.community ?? 0}`,
            n.is_textbook ? "教科书式引用候选" : "",
          ]
            .filter(Boolean)
            .join("<br/>");
        },
      },
      legend: {
        data: categories.map(c => c.name),
        top: 0,
        textStyle: { color: C.chartText(), fontSize: 11 },
      },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          draggable: true,
          categories,
          data: series.nodes,
          links: series.links,
          force: { repulsion: 260, edgeLength: [60, 180], gravity: 0.06 },
          emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
          label: { show: false },
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 6,
          lineStyle: { color: C.line(), width: 1, curveness: 0.08 },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [series, data, theme]);

  if (error) {
    return <div className="muted" style={{ padding: 16 }}>{error}</div>;
  }
  if (!data || !series) {
    return <div className="skeleton" style={{ width: "100%", height }} />;
  }

  const s = data.stats;
  return (
    <div>
      <div className="small muted" style={{ marginBottom: 10 }}>
        {s.paper_count} 篇项目论文 · {s.shared_reference_count} 篇共享参考文献 · {s.coupling_edge_count} 条耦合边 · {s.community_count} 个论文社区 · 教科书式引用候选 {s.textbook_citations.length}
      </div>
      <div ref={ref} style={{ width: "100%", height }} />
      <div className="grid grid-2" style={{ marginTop: 10, alignItems: "start" }}>
        <div className="small muted">
          <b>核心参考文献</b>
          <ol style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {s.core_references.slice(0, 5).map((r, i) => (
              <li key={i}>{r.title ?? "未知"}（{r.co_cited_count} 篇共引{r.is_textbook ? " · 教科书式引用候选" : ""}）</li>
            ))}
          </ol>
        </div>
        <div className="small muted">
          <b>耦合最紧密的论文对</b>
          <ol style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {s.top_coupling_pairs.slice(0, 5).map((p, i) => (
              <li key={i}>{p.a ?? "未知"} ↔ {p.b ?? "未知"}（共享 {p.shared_references} 篇）</li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
