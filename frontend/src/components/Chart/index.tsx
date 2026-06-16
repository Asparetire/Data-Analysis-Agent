import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
  ToolboxComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { getTheme } from '../../hooks/useUi';

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
  ToolboxComponent,
  CanvasRenderer,
]);

interface ChartProps {
  option: EChartsOption;
  height?: number | string;
}

export interface ChartHandle {
  /** Return a PNG data URL of the current chart, suitable for `<a download>`. */
  getPngDataURL: () => string | null;
  /** Force the chart to fit its container -- call after a parent resize. */
  resize: () => void;
}

/**
 * Augment a backend-supplied ECharts option with interactive controls that we
 * want available on every chart (data-zoom brush for category axes, a small
 * toolbox with save-as-image). The backend owns the data shape; we own the UX.
 *
 * Idempotent: if the backend already provided `dataZoom` or `toolbox`, we
 * leave it alone so its choices win.
 */
function decorateOption(option: EChartsOption): EChartsOption {
  const hasXAxis = option.xAxis !== undefined;
  const isPie = Array.isArray(option.series)
    ? option.series.some((s) => (s as { type?: string }).type === 'pie')
    : false;
  // DataZoom only makes sense for cartesian charts.
  const wantDataZoom = hasXAxis && !isPie;
  return {
    ...option,
    tooltip: option.tooltip ?? { trigger: 'axis' },
    ...(wantDataZoom && !option.dataZoom
      ? {
          dataZoom: [
            { type: 'inside', start: 0, end: 100 },
            { type: 'slider', height: 18, bottom: 4 },
          ],
        }
      : {}),
    ...(!option.toolbox
      ? {
          toolbox: {
            right: 8,
            top: 8,
            itemSize: 14,
            feature: {
              dataZoom: { yAxisIndex: 'none' },
              saveAsImage: { name: 'chart' },
            },
          },
        }
      : {}),
  };
}

/**
 * Thin React wrapper around ECharts. The backend's `chart_data` is already
 * an ECharts option dict, so we just hand it over.
 */
const Chart = forwardRef<ChartHandle, ChartProps>(function Chart({ option, height = 320 }, ref) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const decorated = useMemo(() => decorateOption(option), [option]);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const inst = echarts.init(containerRef.current);
    chartRef.current = inst;
    inst.setOption(decorated);
    const onResize = () => inst.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      inst.dispose();
      chartRef.current = null;
    };
  }, [decorated]);

  useImperativeHandle(
    ref,
    () => ({
      getPngDataURL: () =>
        chartRef.current?.getDataURL({
          type: 'png',
          pixelRatio: 2,
          backgroundColor: getTheme() === 'dark' ? '#0f1115' : '#fff',
        }) ?? null,
      resize: () => chartRef.current?.resize(),
    }),
    [],
  );

  return <div ref={containerRef} style={{ width: '100%', height }} />;
});

export default Chart;
