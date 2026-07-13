import { describe, expect, it } from 'vitest'

import {
  buildComparison,
  createDataSource,
  normalizeTimeline,
  type CatalogEntry,
  type RunSet,
} from './benchmark'

const selection = {
  build_profile: 'local-gradle-docker',
  environment_profile: 'home-k3s-v1',
  load_profile: 'steady',
  measurement_protocol: 'official-service-v1',
  scenario: 'read-heavy-query-api',
  variant: 'jvm-java25',
}

function entry(
  implementation: string,
  runSetId: string,
  cohort = 'same-cohort',
  finishedAt = '2026-07-13T10:00:00Z',
  variant = 'jvm-java25',
): CatalogEntry {
  return {
    cohort_fingerprint: cohort,
    evidence_family: 'service',
    finished_at: finishedAt,
    image_digest: `sha256:${implementation}`,
    path: `run-sets/${cohort}/${runSetId}`,
    publication_sha256: 'a'.repeat(64),
    run_set_id: runSetId,
    selection: { ...selection, implementation, variant },
    source_commit: 'b'.repeat(40),
    started_at: '2026-07-13T09:00:00Z',
  }
}

function runSet(runSetId: string, validTrials = 3): RunSet {
  return {
    cohort_fingerprint: 'same-cohort',
    expected_trials: 3,
    finished_at: '2026-07-13T10:00:00Z',
    manifest_digest: 'c'.repeat(64),
    run_set_id: runSetId,
    schema_version: '1.0',
    started_at: '2026-07-13T09:00:00Z',
    status: 'complete',
    summary: {
      trial_count: 3,
      valid_trial_count: validTrials,
      runtime_metrics: {
        rps: { min: 298, median: 300, max: 301, trials: [] },
        p95_ms: { min: 1, median: 1.2, max: 1.5, trials: [] },
      },
      startup_metrics: {},
    },
    trials: [1, 2, 3].map((index) => ({
      index,
      path: `trials/0${index}/trial.json`,
      sha256: 'd'.repeat(64),
      status: index <= validTrials ? 'valid' : 'invalid',
      trial_id: `trial-0${index}`,
    })),
  }
}

describe('buildComparison', () => {
  it('keeps only complete run sets from the same cohort and latest implementation run', () => {
    const springOld = entry(
      'java/spring-boot',
      'spring-old',
      'same-cohort',
      '2026-07-13T08:00:00Z',
    )
    const springNew = entry('java/spring-boot', 'spring-new')
    const quarkus = entry('java/quarkus', 'quarkus-new')
    const incomplete = entry('java/micronaut', 'micronaut-incomplete')
    const incompatible = entry(
      'java/other',
      'other-cohort',
      'different-cohort',
    )
    const springNative = entry(
      'java/spring-boot',
      'spring-native',
      'same-cohort',
      '2026-07-13T09:00:00Z',
      'native-java25',
    )
    const runSets = new Map([
      [springOld.run_set_id, runSet(springOld.run_set_id)],
      [springNew.run_set_id, runSet(springNew.run_set_id)],
      [quarkus.run_set_id, runSet(quarkus.run_set_id)],
      [incomplete.run_set_id, runSet(incomplete.run_set_id, 2)],
      [incompatible.run_set_id, runSet(incompatible.run_set_id)],
      [springNative.run_set_id, runSet(springNative.run_set_id)],
    ])

    const comparison = buildComparison(
      [springOld, springNew, quarkus, incomplete, incompatible, springNative],
      runSets,
      {
        cohort: 'same-cohort',
        loadProfile: 'steady',
        scenario: 'read-heavy-query-api',
      },
    )

    expect(comparison.map((item) => item.entry.run_set_id)).toEqual([
      'quarkus-new',
      'spring-new',
      'spring-native',
    ])
  })
})

describe('normalizeTimeline', () => {
  it('preserves request, latency, error, and resource series in elapsed order', () => {
    const timeline = normalizeTimeline({
      sample_interval_ms: 10_000,
      samples: [
        {
          elapsed_ms: 20_000,
          requested_rps: 600,
          achieved_rps: 598.5,
          error_rate: 0.001,
          p50_ms: 1.1,
          p95_ms: 2.2,
          p99_ms: 4.4,
          target_cpu_percent: 62,
          target_memory_bytes: 268_435_456,
          dependency_cpu_percent: 18,
          dependency_memory_bytes: 134_217_728,
          load_generator_cpu_percent: 12,
          load_generator_memory_bytes: 67_108_864,
        },
        {
          elapsed_ms: 10_000,
          requested_rps: 300,
          achieved_rps: 300,
          error_rate: 0,
          p50_ms: 0.9,
          p95_ms: 1.8,
          p99_ms: 3.2,
          target_cpu_percent: 40,
          target_memory_bytes: 250_000_000,
        },
        { elapsed_ms: -1, achieved_rps: 1 },
      ],
      schema_version: '1.0',
      trial_id: 'trial-01',
    })

    expect(timeline.sampleIntervalMs).toBe(10_000)
    expect(timeline.samples.map((sample) => sample.elapsed_ms)).toEqual([10_000, 20_000])
    expect(timeline.samples[1]).toMatchObject({
      achieved_rps: 598.5,
      dependency_cpu_percent: 18,
      error_rate: 0.001,
      load_generator_cpu_percent: 12,
      p99_ms: 4.4,
      requested_rps: 600,
      target_memory_bytes: 268_435_456,
    })
  })
})

describe('createDataSource', () => {
  it('loads every document from the immutable data revision', async () => {
    const revision = 'e'.repeat(40)
    const requested: string[] = []
    const documents = new Map<string, unknown>([
      ['catalog.json', { schema_version: '1.0', entries: [] }],
      ['run-sets/cohort/run/run-set.json', runSet('run')],
      [
        'run-sets/cohort/run/trials/01/time-series.json',
        { schema_version: '1.0', trial_id: 'trial-01', samples: [] },
      ],
    ])
    const source = createDataSource({
      fetcher: async (input) => {
        const url = String(input)
        requested.push(url)
        const path = url.split(`/${revision}/`)[1]
        const body = documents.get(path)
        return new Response(JSON.stringify(body), {
          status: body ? 200 : 404,
        })
      },
      repository: 'owner/repository',
      revision,
    })

    await source.catalog()
    await source.runSet('run-sets/cohort/run')
    await source.document('run-sets/cohort/run/trials/01/time-series.json')

    expect(requested).toEqual([
      `https://raw.githubusercontent.com/owner/repository/${revision}/catalog.json`,
      `https://raw.githubusercontent.com/owner/repository/${revision}/run-sets/cohort/run/run-set.json`,
      `https://raw.githubusercontent.com/owner/repository/${revision}/run-sets/cohort/run/trials/01/time-series.json`,
    ])
  })

  it('rejects unsafe dataset paths before fetching', async () => {
    const source = createDataSource({
      fetcher: async () => new Response('{}'),
      repository: 'owner/repository',
      revision: 'benchmark-data',
    })

    await expect(source.document('../main/README.md')).rejects.toThrow(
      'Unsafe dataset path',
    )
  })
})
