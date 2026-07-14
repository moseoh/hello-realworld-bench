import { describe, expect, it, vi } from 'vitest'

import {
  buildComparison,
  createDataSource,
  isCompleteBuildRunSet,
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
      [
        'run-sets/cohort/run/run-set.json',
        { ...runSet('run'), cohort_fingerprint: 'cohort' },
      ],
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
    await source.runSet(entry('java/spring-boot', 'run', 'cohort'))
    await source.document('run-sets/cohort/run/trials/01/time-series.json')

    expect(requested).toEqual([
      `https://raw.githubusercontent.com/owner/repository/${revision}/catalog.json`,
      `https://raw.githubusercontent.com/owner/repository/${revision}/run-sets/cohort/run/run-set.json`,
      `https://raw.githubusercontent.com/owner/repository/${revision}/run-sets/cohort/run/trials/01/time-series.json`,
    ])
  })

  it('loads build entries from build-run-set.json without service-only fields', async () => {
    const revision = 'e'.repeat(40)
    const requested: string[] = []
    const buildEntry = {
      cohort_fingerprint: 'build-cohort',
      evidence_family: 'build',
      finished_at: '2026-07-13T10:00:00Z',
      path: 'build-run-sets/build-cohort/build-run',
      publication_sha256: 'a'.repeat(64),
      run_set_id: 'build-run',
      selection: {
        build_profile: 'official-gradle-docker-v1',
        environment_profile: 'home-build-v1',
        implementation: 'java/spring-boot',
        measurement_protocol: 'official-build-v1',
        variant: 'jvm-java25',
      },
      source_commit: 'b'.repeat(40),
      started_at: '2026-07-13T09:00:00Z',
    } as unknown as CatalogEntry
    const source = createDataSource({
      fetcher: async (input) => {
        const url = String(input)
        requested.push(url)
        return new Response(JSON.stringify(buildRunSet('build-run', 'build-cohort')))
      },
      repository: 'owner/repository',
      revision,
    })

    await (source.runSet as unknown as (entry: CatalogEntry) => Promise<unknown>)(buildEntry)

    expect(requested).toEqual([
      `https://raw.githubusercontent.com/owner/repository/${revision}/build-run-sets/build-cohort/build-run/build-run-set.json`,
    ])
  })

  it.each([
    '../main/README.md',
    '%2e%2e/%2e%2e/attacker/repo/main/catalog.json',
    'run-sets/cohort%2frun/run-set.json',
    'run-sets/cohort%5crun/run-set.json',
    'run-sets/cohort/run-set.json?raw=1',
    'run-sets/cohort/run-set.json#fragment',
    'run-sets/cohort/\u0000run-set.json',
  ])('rejects unsafe dataset path %j before fetching', async (unsafePath) => {
    const fetcher = vi.fn(async () => new Response('{}'))
    const source = createDataSource({
      fetcher,
      repository: 'owner/repository',
      revision: 'benchmark-data',
    })

    await expect(source.document(unsafePath)).rejects.toThrow(
      'Unsafe dataset path',
    )
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('filters malformed catalog entries and binds run sets to catalog identity', async () => {
    const valid = entry('java/spring-boot', 'run', 'cohort')
    const encodedIdentity = {
      ...valid,
      cohort_fingerprint: '%2e%2e',
      path: 'run-sets/%2e%2e/run',
    }
    const source = createDataSource({
      fetcher: async (input) => {
        const path = String(input).split('/benchmark-data/')[1]
        const body = path === 'catalog.json'
          ? {
              schema_version: '1.0',
              entries: [valid, encodedIdentity, { evidence_family: 'build' }],
            }
          : { ...runSet('other-run'), cohort_fingerprint: 'cohort' }
        return new Response(JSON.stringify(body))
      },
      repository: 'owner/repository',
      revision: 'benchmark-data',
    })

    await expect(source.catalog()).resolves.toEqual([valid])
    await expect(source.runSet(valid)).rejects.toThrow('does not match catalog entry')
  })
})

describe('isCompleteBuildRunSet', () => {
  it('requires exactly three ordered trials and recomputed non-negative summaries', () => {
    const valid = buildRunSet('build-run', 'build-cohort')
    expect(isCompleteBuildRunSet(valid, 'build-cohort')).toBe(true)

    const oneTrial = structuredClone(valid)
    oneTrial.expected_trials = 1
    oneTrial.trials = oneTrial.trials.slice(0, 1)
    oneTrial.summary.trial_count = 1
    oneTrial.summary.valid_trial_count = 1
    expect(isCompleteBuildRunSet(oneTrial, 'build-cohort')).toBe(false)

    const duplicate = structuredClone(valid)
    duplicate.trials[1] = { ...duplicate.trials[0] }
    expect(isCompleteBuildRunSet(duplicate, 'build-cohort')).toBe(false)

    const wrongSummary = structuredClone(valid)
    wrongSummary.summary.build_metrics.image_rebuild_ms.median += 1
    expect(isCompleteBuildRunSet(wrongSummary, 'build-cohort')).toBe(false)

    const negative = structuredClone(valid)
    negative.summary.build_metrics.image_package_ms.trials[0].value = -1
    expect(isCompleteBuildRunSet(negative, 'build-cohort')).toBe(false)

    expect(() => isCompleteBuildRunSet({ summary: null } as never, 'build-cohort')).not.toThrow()
    expect(isCompleteBuildRunSet({ summary: null } as never, 'build-cohort')).toBe(false)
  })
})

function buildRunSet(runSetId: string, cohort: string) {
  const metric = (value: number) => ({
    min: value - 1,
    median: value,
    max: value + 1,
    trials: [
      { trial_id: 'trial-01', value: value - 1 },
      { trial_id: 'trial-02', value },
      { trial_id: 'trial-03', value: value + 1 },
    ],
  })
  return {
    cohort_fingerprint: cohort,
    expected_trials: 3,
    manifest_digest: 'c'.repeat(64),
    run_set_id: runSetId,
    schema_version: '1.0',
    status: 'complete',
    summary: {
      build_metrics: {
        gradle_clean_build_ms: metric(10),
        gradle_incremental_rebuild_ms: metric(20),
        image_package_ms: metric(30),
        image_rebuild_ms: metric(40),
      },
      trial_count: 3,
      valid_trial_count: 3,
    },
    trials: [1, 2, 3].map((index) => ({
      index,
      path: `trials/0${index}/build-trial.json`,
      sha256: 'd'.repeat(64),
      status: 'valid',
      trial_id: `trial-0${index}`,
    })),
  }
}
