import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  CanvasRenderer,
]);

interface ChartProps {
  option: EChartsOption;
  height?: number | string;
}

/**
 * Thin React wrapper around ECharts. The backend's `chart_data` is already
 * an ECharts option dict, so we just hand it over.
 */
export default function Chart({ option, height = 320 }: ChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const inst = echarts.init(containerRef.current);
    chartRef.current = inst;
    inst.setOption(option);
    const onResize = () => inst.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      inst.dispose();
      chartRef.current = null;
    };
  }, [option]);

  return <div ref={containerRef} style={{ width: '100%', height }} />;
}
