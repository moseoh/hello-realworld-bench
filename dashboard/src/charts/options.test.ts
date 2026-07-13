import { describe, expect, it } from 'vitest'

import type { TimelineSample } from '../data/benchmark'
import {
  errorOption,
  latencyOption,
  memoryOption,
  resourceOption,
  trafficOption,
} from './options'

const samples: TimelineSample[] = [
  {
    elapsed_ms: 10_000,
    requested_rps: 300,
    achieved_rps: 299,
    error_rate: 0.01,
    p50_ms: 1,
    p95_ms: 2,
    p99_ms: 3,
    target_cpu_percent: 40,
    target_memory_bytes: 256 * 1024 * 1024,
    dependency_cpu_percent: 15,
    dependency_memory_bytes: 128 * 1024 * 1024,
    load_generator_cpu_percent: 10,
    load_generator_memory_bytes: 64 * 1024 * 1024,
  },
]

function series(option: unknown) {
  return (option as { series: Array<{ data: unknown[]; name: string }> }).series
}

describe('timeline chart options', () => {
  it('maps requested and achieved traffic without changing units', () => {
    expect(series(trafficOption(samples))).toMatchObject([
      { data: [[10, 300]], name: 'Requested' },
      { data: [[10, 299]], name: 'Achieved' },
    ])
  })

  it('keeps missing samples as gaps instead of connecting across them', () => {
    const gapped: TimelineSample[] = [
      samples[0],
      { elapsed_ms: 20_000 },
      { elapsed_ms: 30_000, requested_rps: 400, achieved_rps: 390 },
    ]

    expect(series(trafficOption(gapped))[0].data).toEqual([
      [10, 300],
      [20, null],
      [30, 400],
    ])
  })

  it('keeps latency percentiles as separate series', () => {
    expect(series(latencyOption(samples))).toMatchObject([
      { data: [[10, 1]], name: 'p50' },
      { data: [[10, 2]], name: 'p95' },
      { data: [[10, 3]], name: 'p99' },
    ])
  })

  it('converts error ratio to percent', () => {
    expect(series(errorOption(samples))[0]).toMatchObject({
      data: [[10, 1]],
      name: 'Errors',
    })
  })

  it('separates target, dependency, and load-generator resources', () => {
    expect(series(resourceOption(samples)).map((item) => item.name)).toEqual([
      'Target',
      'Dependency',
      'Load generator',
    ])
    expect(series(memoryOption(samples))).toMatchObject([
      { data: [[10, 256]], name: 'Target' },
      { data: [[10, 128]], name: 'Dependency' },
      { data: [[10, 64]], name: 'Load generator' },
    ])
  })
})
