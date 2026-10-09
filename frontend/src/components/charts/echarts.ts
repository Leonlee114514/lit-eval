// 按需注册 ECharts 模块，减小打包体积（对比全量 `import * as echarts from "echarts"`）
// 本项目只用：仪表盘 / 雷达 / 平行坐标 / 树图 + Tooltip + Canvas
import * as echarts from "echarts/core";
import {
  GaugeChart,
  GraphChart,
  RadarChart as RadarChartComp,
  ParallelChart,
  TreeChart,
} from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  GaugeChart,
  GraphChart,
  RadarChartComp,
  ParallelChart,
  TreeChart,
  TooltipComponent,
  CanvasRenderer,
]);

export { echarts };
