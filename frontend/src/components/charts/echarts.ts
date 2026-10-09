// 按需注册 ECharts 模块，减小打包体积（对比全量 `import * as echarts from "echarts"`）
// 本项目用到：仪表盘 / 雷达 / 平行坐标 / 树图 / 关系图（GNN 引用网络）+ Tooltip + Legend + Canvas
import * as echarts from "echarts/core";
import {
  GaugeChart,
  GraphChart,
  RadarChart as RadarChartComp,
  ParallelChart,
  TreeChart,
} from "echarts/charts";
import { LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  GaugeChart,
  GraphChart,
  RadarChartComp,
  ParallelChart,
  TreeChart,
  TooltipComponent,
  // 缺 LegendComponent 时，配了 legend 的图（项目 GNN 引用网络）会报
  // "[ECharts] Component legend is used but not imported" 且图例不显示
  LegendComponent,
  CanvasRenderer,
]);

export { echarts };
