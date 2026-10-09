import { useEffect, useRef } from "react";
import { echarts } from "./echarts";
import { C } from "../../theme";
import { useThemeStore } from "../../store/useThemeStore";
import { api } from "../../api/client";

interface Node {
  id?: string;
  title?: string | null;
  cited_by_count?: number | null;
  publication_year?: number | null;
}

interface Graph {
  root: Node;
  citing: Node[];
  references: Node[];
}

interface Props {
  paperId: string;
  height?: number;
}

const MAX_PER_SIDE = 5;

function shortTitle(n: Node): string {
  const title = n.title || "未知文献";
  return title.length > 24 ? `${title.slice(0, 23)}…` : title;
}

function spread(center: number, count: number, min = 14, max = 86): number[] {
  if (count <= 1) return [center];
  const step = (max - min) / (count - 1);
  return Array.from({ length: count }, (_, i) => Math.round(min + step * i));
}

export default function CitationTree({ paperId, height = 460 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useThemeStore(s => s.theme);

  useEffect(() => {
    let disposed = false;
    let chart: ReturnType<typeof echarts.init> | null = null;

    api
      .getCitationGraph(paperId)
      .then(raw => {
        const graph = raw as Graph | null;
        if (disposed || !ref.current || !graph) return;
        chart = echarts.init(ref.current);

        const citing = graph.citing.slice(0, MAX_PER_SIDE);
        const refs = graph.references.slice(0, MAX_PER_SIDE);

        // 手工布局：根节点居中，左列=引用本文，右列=本文引用
        const rootX = 50;
        const citingYs = spread(50, citing.length);
        const refYs = spread(50, refs.length);

        const byId = new Map<string, Node>();
        const nodes: Array<Record<string, unknown>> = [
          {
            id: "root",
            name: shortTitle(graph.root),
            x: rootX,
            y: 50,
            symbolSize: 42,
            category: 0,
            label: { show: true, position: "bottom", distance: 10, color: C.gaugeText(), fontSize: 12, fontWeight: 700 },
            tooltipRaw: graph.root,
          },
        ];
        byId.set("root", graph.root);

        citing.forEach((n, i) => {
          const id = n.id || `citing-${i}`;
          nodes.push({
            id,
            name: shortTitle(n),
            x: 13,
            y: citingYs[i],
            symbolSize: Math.min(34, 12 + Math.log2((n.cited_by_count ?? 0) + 1) * 3.2),
            category: 1,
            label: { show: true, position: "right", distance: 8, color: C.chartText(), fontSize: 11 },
            tooltipRaw: n,
          });
          byId.set(id, n);
        });

        refs.forEach((n, i) => {
          const id = n.id || `ref-${i}`;
          nodes.push({
            id,
            name: shortTitle(n),
            x: 87,
            y: refYs[i],
            symbolSize: Math.min(34, 12 + Math.log2((n.cited_by_count ?? 0) + 1) * 3.2),
            category: 2,
            label: { show: true, position: "left", distance: 8, color: C.chartText(), fontSize: 11 },
            tooltipRaw: n,
          });
          byId.set(id, n);
        });

        const links: Array<Record<string, unknown>> = [];
        citing.forEach((n, i) => {
          links.push({ source: n.id || `citing-${i}`, target: "root" });
        });
        refs.forEach((n, i) => {
          links.push({ source: "root", target: n.id || `ref-${i}` });
        });

        chart.setOption({
          tooltip: {
            formatter: (p: any) => {
              const raw = (p.data as any)?.tooltipRaw as Node | undefined;
              if (!raw) return p.name;
              return [
                `<b>${raw.title || "未知文献"}</b>`,
                `${raw.publication_year ?? "年份未知"} · 被引 ${raw.cited_by_count ?? 0}`,
              ].join("<br/>");
            },
          },
          legend: {
            data: ["本文", "引用本文", "本文引用"],
            top: 0,
            textStyle: { color: C.chartText(), fontSize: 11 },
          },
          series: [
            {
              type: "graph",
              layout: "none",
              roam: true,
              draggable: false,
              categories: [
                { name: "本文", itemStyle: { color: C.primary() } },
                { name: "引用本文", itemStyle: { color: C.yellow() } },
                { name: "本文引用", itemStyle: { color: C.green() } },
              ],
              data: nodes,
              links,
              symbol: "circle",
              edgeSymbol: ["none", "arrow"],
              edgeSymbolSize: 7,
              lineStyle: { color: C.line(), width: 1.2, curveness: 0.18, opacity: 0.85 },
              emphasis: { focus: "adjacency", lineStyle: { width: 2.5, opacity: 1 } },
              scaleLimit: { min: 0.7, max: 2.2 },
            },
          ],
        });

        const onResize = () => chart?.resize();
        window.addEventListener("resize", onResize);
      })
      .catch(() => {
        if (!disposed && ref.current) {
          ref.current.innerHTML = '<div class="muted" style="padding:16px">引用树加载失败（该论文可能无 OpenAlex 标识）</div>';
        }
      });

    return () => {
      disposed = true;
      if (chart) chart.dispose();
    };
  }, [paperId, theme]);

  return (
    <div>
      <div className="small muted" style={{ display: "flex", gap: 14, marginBottom: 6 }}>
        <span><span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 999, background: C.yellow(), marginRight: 5 }} />引用本文</span>
        <span><span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 999, background: C.primary(), marginRight: 5 }} />本文</span>
        <span><span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 999, background: C.green(), marginRight: 5 }} />本文引用</span>
      </div>
      <div ref={ref} style={{ width: "100%", height }} />
    </div>
  );
}
