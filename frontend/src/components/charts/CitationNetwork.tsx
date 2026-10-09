import { useEffect, useMemo, useRef, useState } from "react";
import { echarts } from "./echarts";
import { C } from "../../theme";
import { useThemeStore } from "../../store/useThemeStore";
import { api } from "../../api/client";
import type { CitationNetworkData, CitationNetworkNode } from "../../api/types";

interface Props {
  paperId: string;
  height?: number;
}

const ROLE_LABEL: Record<string, string> = { root: "本文", citing: "施引文献", reference: "参考文献" };

function nodeName(n: CitationNetworkNode): string {
  return (n.title || n.id || "未知").slice(0, 32);
}

function fmt(n: number | undefined | null): string {
  return n == null ? "—" : String(Number(n.toFixed(4)));
}

export default function CitationNetwork({ paperId, height = 440 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<CitationNetworkData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const theme = useThemeStore(s => s.theme);

  useEffect(() => {
    let disposed = false;
    setError(null);
    api
      .getCitationNetwork(paperId)
      .then(raw => {
        if (!disposed) setData(raw ?? null);
      })
      .catch(() => {
        if (!disposed) setError("GNN 引用网络加载失败（该论文可能无 OpenAlex 标识）");
      });
    return () => {
      disposed = true;
    };
  }, [paperId]);

  const series = useMemo(() => {
    if (!data) return null;
    const nodes = data.nodes.map(n => ({
      id: n.id,
      name: `${nodeName(n)}\n${n.publication_year ?? "?"} · 被引 ${n.cited_by_count}`,
      symbolSize:
        n.role === "root"
          ? 48
          : Math.min(38, 10 + Math.log2((n.cited_by_count || 0) + 1) * 4),
      category: n.role === "root" ? 0 : n.role === "reference" ? 1 : 2,
      value: Math.max(1, (n.pagerank ?? 0) * 1000),
      raw: n,
    }));
    const links = data.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      lineStyle: { width: e.type === "cites" ? 1 : 1, opacity: 0.35 },
    }));
    return { nodes, links };
  }, [data]);

  useEffect(() => {
    if (!ref.current || !series || !data) return;
    const chart = echarts.init(ref.current);
    const categories = [
      { name: "本文", itemStyle: { color: C.primary() } },
      { name: "参考文献", itemStyle: { color: C.green() } },
      { name: "施引文献", itemStyle: { color: C.yellow() } },
    ];
    chart.setOption({
      tooltip: {
        formatter: (p: any) => {
          const n = p.data?.raw as CitationNetworkNode | undefined;
          if (!n) return p.name;
          return [
            `<b>${n.title || n.id}</b>`,
            `${ROLE_LABEL[n.role] ?? n.role} · ${n.publication_year ?? "年份未知"} · 被引 ${n.cited_by_count}`,
            `PageRank ${fmt(n.pagerank)} · 社区 #${n.community ?? 0}`,
            n.is_textbook ? "教科书式引用候选" : "",
            n.co_cited_count ? `共被引支持 ${n.co_cited_count}` : "",
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
          force: { repulsion: 220, edgeLength: [50, 140], gravity: 0.08 },
          emphasis: { focus: "adjacency", lineStyle: { width: 2 } },
          label: { show: false },
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 6,
          lineStyle: { color: C.line(), width: 1, curveness: 0.12 },
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
        {s.node_count} 节点 · {s.edge_count} 边 · {s.community_count} 个群落 · 本文 PageRank 第{" "}
        {s.root_pagerank_rank}/{s.node_count} · 教科书式引用候选 {s.textbook_citations.length}
      </div>
      <div ref={ref} style={{ width: "100%", height }} />
      {s.citing_with_bibliographic_coupling.length > 0 && (
        <div className="small muted" style={{ marginTop: 8 }}>
          文献耦合最强：{s.citing_with_bibliographic_coupling.slice(0, 3).map(c =>
            `${c.title ?? "未知"}（共享 ${c.shared_references} 篇）`).join("；")}
        </div>
      )}
      {s.co_cited_references.length > 0 && (
        <div className="small muted" style={{ marginTop: 6 }}>
          共被引支持：{s.co_cited_references.slice(0, 3).map(c =>
            `${c.title ?? "未知"}（${c.co_cited_count}${c.is_textbook ? " · 教科书式引用候选" : ""}）`).join("；")}
        </div>
      )}
    </div>
  );
}
