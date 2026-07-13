import type { TimelineSample } from '../data/benchmark'

export interface TimelineChartOption {
  animation: boolean
  color: string[]
  grid: Record<string, number>
  legend: Record<string, unknown>
  series: Array<Record<string, unknown>>
  tooltip: Record<string, unknown>
  xAxis: Record<string, unknown>
  yAxis: Record<string, unknown>
}

const COLORS = ['#0b7a75', '#d97706', '#c24156', '#2463a8']

export function trafficOption(samples: TimelineSample[]): TimelineChartOption {
  return lineOption(
    samples,
    [
      ['Requested', 'requested_rps'],
      ['Achieved', 'achieved_rps'],
    ],
    'req/s',
  )
}

export function latencyOption(samples: TimelineSample[]): TimelineChartOption {
  return lineOption(
    samples,
    [
      ['p50', 'p50_ms'],
      ['p95', 'p95_ms'],
      ['p99', 'p99_ms'],
    ],
    'ms',
  )
}

export function errorOption(samples: TimelineSample[]): TimelineChartOption {
  return lineOption(samples, [['Errors', 'error_rate', (value) => value * 100]], '%')
}

export function resourceOption(samples: TimelineSample[]): TimelineChartOption {
  return lineOption(
    samples,
    [
      ['Target', 'target_cpu_percent'],
      ['Dependency', 'dependency_cpu_percent'],
      ['Load generator', 'load_generator_cpu_percent'],
    ],
    '% CPU',
  )
}

export function memoryOption(samples: TimelineSample[]): TimelineChartOption {
  const mib = (value: number) => value / 1024 / 1024
  return lineOption(
    samples,
    [
      ['Target', 'target_memory_bytes', mib],
      ['Dependency', 'dependency_memory_bytes', mib],
      ['Load generator', 'load_generator_memory_bytes', mib],
    ],
    'MiB',
  )
}

type NumericTimelineField = Exclude<
  keyof TimelineSample,
  'elapsed_ms'
>
type SeriesDefinition = [
  name: string,
  field: NumericTimelineField,
  transform?: (value: number) => number,
]

function lineOption(
  samples: TimelineSample[],
  definitions: SeriesDefinition[],
  unit: string,
): TimelineChartOption {
  return {
    animation: false,
    color: COLORS,
    grid: { bottom: 42, left: 58, right: 22, top: 42 },
    legend: {
      icon: 'roundRect',
      itemHeight: 3,
      itemWidth: 16,
      left: 0,
      textStyle: { color: '#59636d', fontSize: 11 },
      top: 0,
    },
    series: definitions.map(([name, field, transform]) => ({
      connectNulls: false,
      data: samples.map((sample) => {
        const value = sample[field]
        return [
          sample.elapsed_ms / 1000,
          typeof value === 'number'
            ? transform
              ? transform(value)
              : value
            : null,
        ]
      }),
      emphasis: { focus: 'series' },
      lineStyle: { width: 2 },
      name,
      showSymbol: false,
      type: 'line',
    })),
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (value: unknown) =>
        typeof value === 'number' ? `${formatValue(value)} ${unit}` : 'n/a',
    },
    xAxis: {
      axisLabel: {
        color: '#68737d',
        formatter: (value: number) => formatElapsed(value),
      },
      axisLine: { lineStyle: { color: '#ccd3d9' } },
      boundaryGap: false,
      min: 0,
      splitLine: { show: false },
      type: 'value',
    },
    yAxis: {
      axisLabel: { color: '#68737d' },
      name: unit,
      nameGap: 10,
      nameTextStyle: { color: '#68737d', fontSize: 11 },
      scale: true,
      splitLine: { lineStyle: { color: '#e7ebee' } },
      type: 'value',
    },
  }
}

function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return minutes > 0 ? `${minutes}m${remainder ? ` ${remainder}s` : ''}` : `${remainder}s`
}

function formatValue(value: number): string {
  return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(2)
}
