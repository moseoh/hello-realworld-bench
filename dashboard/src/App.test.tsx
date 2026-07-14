import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const fixture = vi.hoisted(() => {
  const metric = (value: number) => ({
    min: value - 100,
    median: value,
    max: value + 100,
    trials: [
      { trial_id: 'trial-01', value: value - 100 },
      { trial_id: 'trial-02', value },
      { trial_id: 'trial-03', value: value + 100 },
    ],
  })
  const entry = {
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
  }
  const lifecycleEntry = {
    cohort_fingerprint: 'lifecycle-cohort',
    evidence_family: 'lifecycle',
    finished_at: '2026-07-13T10:00:00Z',
    image_digest: 'sha256:image',
    path: 'run-sets/lifecycle-cohort/lifecycle-run',
    publication_sha256: 'd'.repeat(64),
    run_set_id: 'lifecycle-run',
    selection: {
      build_profile: 'official-gradle-docker-v1',
      environment_profile: 'home-k3s-lifecycle-v1',
      implementation: 'java/quarkus',
      load_profile: 'none',
      measurement_protocol: 'official-cold-start-v1',
      scenario: 'cold-start-api',
      variant: 'jvm-java25',
    },
    source_commit: 'e'.repeat(40),
    started_at: '2026-07-13T09:00:00Z',
  }
  const lifecycleRunSet = {
    cohort_fingerprint: 'lifecycle-cohort',
    expected_trials: 3,
    finished_at: '2026-07-13T10:00:00Z',
    manifest_digest: 'f'.repeat(64),
    run_set_id: 'lifecycle-run',
    schema_version: '1.0',
    started_at: '2026-07-13T09:00:00Z',
    status: 'complete',
    summary: {
      runtime_metrics: {},
      startup_metrics: { ready_ms: metric(1_000) },
      trial_count: 3,
      valid_trial_count: 3,
    },
    trials: [1, 2, 3].map((index) => ({
      index,
      path: `trials/0${index}/trial.json`,
      sha256: 'c'.repeat(64),
      status: 'valid',
      trial_id: `trial-0${index}`,
    })),
  }
  return {
    dataSource: {
      catalog: vi.fn(async () => [entry, lifecycleEntry]),
      document: vi.fn(async (path: string) => {
        throw new Error(`Unexpected evidence request: ${path}`)
      }),
      runSet: vi.fn(async (catalogEntry: { evidence_family?: string }) => catalogEntry.evidence_family === 'lifecycle' ? lifecycleRunSet : ({
        cohort_fingerprint: 'build-cohort',
        expected_trials: 3,
        run_set_id: 'build-run',
        status: 'complete',
        summary: {
          build_metrics: {
            gradle_clean_build_ms: metric(10_000),
            gradle_incremental_rebuild_ms: metric(2_000),
            image_package_ms: metric(4_000),
            image_rebuild_ms: metric(1_000),
          },
          trial_count: 3,
          valid_trial_count: 3,
        },
        trials: [1, 2, 3].map((index) => ({
          index,
          path: `trials/0${index}/build-trial.json`,
          sha256: 'c'.repeat(64),
          status: 'valid',
          trial_id: `trial-0${index}`,
        })),
      })),
    },
  }
})

vi.mock('./data/benchmark', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./data/benchmark')>()
  return { ...actual, createDataSource: () => fixture.dataSource }
})

import App from './App'

describe('App family views', () => {
  afterEach(() => {
    cleanup()
    window.history.replaceState(null, '', '/')
    fixture.dataSource.document.mockClear()
  })

  it('renders build metric summaries without requesting service evidence or timelines', async () => {
    window.history.replaceState(null, '', '/?family=build')

    render(<App />)

    await screen.findByRole('heading', { name: 'Build' })
    expect(screen.getByText('Gradle clean build')).toBeTruthy()
    expect(screen.getByText('Gradle incremental rebuild')).toBeTruthy()
    expect(screen.getByText('Image package')).toBeTruthy()
    expect(screen.getByText('Image rebuild')).toBeTruthy()
    expect(screen.getByText('10.00 s')).toBeTruthy()
    await waitFor(() => expect(fixture.dataSource.document).not.toHaveBeenCalled())
  })

  it('renders lifecycle startup summaries without requesting service evidence or timelines', async () => {
    window.history.replaceState(null, '', '/?family=lifecycle')

    render(<App />)

    await screen.findByRole('heading', { name: 'Cold start' })
    expect(screen.getByText('Ready Ms')).toBeTruthy()
    expect(screen.getByText('1.00 s')).toBeTruthy()
    await waitFor(() => expect(fixture.dataSource.document).not.toHaveBeenCalled())
  })
})
