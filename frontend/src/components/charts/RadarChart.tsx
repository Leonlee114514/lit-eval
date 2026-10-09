import { useEffect, useRef } from "react";
import { echarts } from "./echarts";
import { C } from "../../theme";
import { useThemeStore } from "../../store/useThemeStore";

const LABELS = ["期刊水平", "引用影响力", "内容质量", "活跃度", "相关性"];
const KEYS = ["journal_level", "citation_impact", "content_quality", "timeliness", "relevance"];

interface Props {
  scores: Record<string, number>;
  height?: number;
}

export default function RadarChart({ scores, height = 320 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useThemeStore(s => s.theme); // 主题变化时重建取新 token 色

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const values = KEYS.map(k => scores[k] ?? 0);
    chart.setOption({
      tooltip: {},
      radar: {
        indicator: LABELS.map((name, i) => ({ name, max: 100 })),
        radius: "68%",
        axisName: { color: C.chartText() },
      },
      series: [
        {
          type: "radar",
          data: [{ value: values, name: "综合评估" }],
          areaStyle: { opacity: 0.25 },
          lineStyle: { width: 2 },
          itemStyle: { color: C.primary() },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [scores, theme]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
