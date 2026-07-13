import { describe, expect, it } from 'vitest'

import type { CatalogEntry, RunSet } from './benchmark'
import {
  listComparisonGroups,
  selectComparisonGroup,
  summarizeTrialResources,
} from './view-model'

function entry(
  implementation: string,
  runSetId: string,
  scenario = 'read-heavy-query-api',
  loadProfile = 'steady',
  cohort = 'cohort-a',
  finishedAt = '2026-07-13T10:00:00Z',
): CatalogEntry {
  return {
    cohort_fingerprint: cohort,
    evidence_family: 'service',
    finished_at: finishedAt,
    image_digest: 'sha256:image',
    path: `run-sets/${cohort}/${runSetId}`,
    publication_sha256: 'a'.repeat(64),
    run_set_id: runSetId,
    selection: {
      build_profile: 'build',
      environment_profile: 'home-k3s-v1',
      implementation,
      load_profile: loadProfile,
      measurement_protocol: 'official-service-v1',
      scenario,
      variant: 'jvm-java25',
    },
    source_commit: 'b'.repeat(40),
    started_at: '2026-07-13T09:00:00Z',
  }
}

function runSet(id: string): RunSet {
  return {
    cohort_fingerprint: 'cohort-a',
    expected_trials: 3,
    finished_at: '2026-07-13T10:00:00Z',
    manifest_digest: 'c'.repeat(64),
    run_set_id: id,
    schema_version: '1.0',
    started_at: '2026-07-13T09:00:00Z',
    status: 'complete',
    summary: {
      runtime_metrics: {},
      startup_metrics: {},
      trial_count: 3,
      valid_trial_count: 3,
    },
    trials: [1, 2, 3].map((index) => ({
      index,
      path: `trials/0${index}/trial.json`,
      sha256: 'd'.repeat(64),
      status: 'valid',
      trial_id: `trial-0${index}`,
    })),
  }
}

describe('listComparisonGroups', () => {
  it('keeps lifecycle evidence out of the service comparison view', () => {
    const service = entry('java/spring-boot', 'service')
    delete service.evidence_family
    const lifecycle = {
      ...entry(
        'java/quarkus',
        'lifecycle',
        'cold-start-api',
        'none',
        'cold-cohort',
      ),
      evidence_family: 'lifecycle',
    }
    const groups = listComparisonGroups(
      [service, lifecycle],
      new Map([
        ['service', runSet('service')],
        [
          'lifecycle',
          { ...runSet('lifecycle'), cohort_fingerprint: 'cold-cohort' },
        ],
      ]),
    )

    expect(groups).toHaveLength(1)
    expect(groups[0].scenario).toBe('read-heavy-query-api')
  })

  it('groups exact cohorts and prioritizes comparable recent groups', () => {
    const entries = [
      entry('java/spring-boot', 'spring'),
      entry('java/quarkus', 'quarkus'),
      entry(
        'java/spring-boot',
        'transactional',
        'transactional-command-api',
      ),
    ]
    const runSets = new Map(entries.map((item) => [item.run_set_id, runSet(item.run_set_id)]))

    const groups = listComparisonGroups(entries, runSets)

    expect(groups[0]).toMatchObject({
      cohort: 'cohort-a',
      implementationCount: 2,
      loadProfile: 'steady',
      scenario: 'read-heavy-query-api',
    })
    expect(groups[1].scenario).toBe('transactional-command-api')
  })

  it('selects an exact cohort when old and new contracts coexist', () => {
    const oldEntry = entry(
      'java/spring-boot',
      'old',
      'read-heavy-query-api',
      'steady',
      'cohort-old',
      '2026-07-12T10:00:00Z',
    )
    const newEntry = entry(
      'java/spring-boot',
      'new',
      'read-heavy-query-api',
      'steady',
      'cohort-new',
      '2026-07-13T10:00:00Z',
    )
    const oldRun = { ...runSet('old'), cohort_fingerprint: 'cohort-old' }
    const newRun = { ...runSet('new'), cohort_fingerprint: 'cohort-new' }
    const groups = listComparisonGroups(
      [oldEntry, newEntry],
      new Map([
        ['old', oldRun],
        ['new', newRun],
      ]),
    )

    expect(
      selectComparisonGroup(groups, {
        cohort: 'cohort-old',
        loadProfile: 'steady',
        scenario: 'read-heavy-query-api',
      })?.cohort,
    ).toBe('cohort-old')
    expect(
      selectComparisonGroup(groups, {
        cohort: '',
        loadProfile: 'steady',
        scenario: 'read-heavy-query-api',
      })?.cohort,
    ).toBe('cohort-new')
  })
})

describe('summarizeTrialResources', () => {
  it('uses medians across valid numeric trial results', () => {
    const summary = summarizeTrialResources([
      {
        runtime_metrics: {
          cpu_percent_avg: 20,
          cpu_percent_max: 50,
          memory_usage_max_bytes: 300 * 1024 * 1024,
        },
      },
      {
        runtime_metrics: {
          cpu_percent_avg: 10,
          cpu_percent_max: 45,
          memory_usage_max_bytes: 280 * 1024 * 1024,
        },
      },
      {
        runtime_metrics: {
          cpu_percent_avg: 30,
          cpu_percent_max: 70,
          memory_usage_max_bytes: 320 * 1024 * 1024,
        },
      },
    ])

    expect(summary).toEqual({
      cpuAveragePercent: 20,
      cpuMaxPercent: 50,
      memoryMaxBytes: 300 * 1024 * 1024,
    })
  })
})
