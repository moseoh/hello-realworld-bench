import { useEffect, useRef } from 'react'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

import type { TimelineChartOption } from './options'

echarts.use([
  CanvasRenderer,
  GridComponent,
  LegendComponent,
  LineChart,
  TooltipComponent,
])

interface TimelineChartProps {
  label: string
  option: TimelineChartOption
}

export function TimelineChart({ label, option }: TimelineChartProps) {
  const container = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!container.current) {
      return
    }
    const instance = echarts.init(container.current, undefined, {
      renderer: 'canvas',
    })
    chart.current = instance
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(container.current)

    return () => {
      observer.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option as unknown as EChartsCoreOption, true)
  }, [option])

  const accessibleSeries = option.series.map((item) => ({
    data: Array.isArray(item.data) ? item.data : [],
    name: typeof item.name === 'string' ? item.name : 'Series',
  }))
  const rowCount = Math.max(0, ...accessibleSeries.map((item) => item.data.length))

  return (
    <>
      <div aria-hidden="true" className="timeline-chart" ref={container} />
      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th>Elapsed seconds</th>
            {accessibleSeries.map((item) => <th key={item.name}>{item.name}</th>)}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rowCount }, (_, index) => {
            const firstPoint = point(accessibleSeries[0]?.data[index])
            return (
              <tr key={`${firstPoint?.[0] ?? index}`}>
                <td>{firstPoint?.[0] ?? 'n/a'}</td>
                {accessibleSeries.map((item) => {
                  const value = point(item.data[index])?.[1]
                  return <td key={item.name}>{typeof value === 'number' ? value : 'n/a'}</td>
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

function point(value: unknown): [number, number | null] | null {
  if (
    Array.isArray(value) &&
    typeof value[0] === 'number' &&
    (typeof value[1] === 'number' || value[1] === null)
  ) {
    return [value[0], value[1]]
  }
  return null
}
