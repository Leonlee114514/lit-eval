import { useEffect, useRef } from "react";
import { echarts } from "./echarts";
import { C } from "../../theme";
import { useThemeStore } from "../../store/useThemeStore";

interface Props {
  score: number | null;
  height?: number;
}

export default function ScoreGauge({ score, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useThemeStore(s => s.theme); // 主题变化时重建图表取新 token 色

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const value = score ?? 0;
    // 与 tier 阈值一致：≥85 绿 / 60-84 绿 / 35-59 黄 / <35 红
    const color = value < 35 ? C.red() : value < 60 ? C.yellow() : C.green();
    chart.setOption({
      series: [
        {
          type: "gauge",
          min: 0,
          max: 100,
          progress: { show: true, width: 14, itemStyle: { color } },
          axisLine: { lineStyle: { width: 14, color: [[1, C.track()]] } },
          pointer: { itemStyle: { color } },
          detail: {
            valueAnimation: true,
            formatter: "{value}",
            fontSize: 28,
            color: C.gaugeText(),
            offsetCenter: [0, "60%"],
          },
          data: [{ value, name: "综合质量分" }],
          title: { offsetCenter: [0, "85%"], color: C.muted(), fontSize: 13 },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [score, theme]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
