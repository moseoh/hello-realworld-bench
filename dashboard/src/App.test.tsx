import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const fixture = vi.hoisted(() => {
  const revision = 'benchmark-data'
  const metric = (value: number, trialCount: number) => ({
    min: value - 100,
    median: value,
    max: value + 100,
    trials: Array.from({ length: trialCount }, (_, offset) => ({
      trial_id: `trial-${String(offset + 1).padStart(2, '0')}`,
      value: value - 100 + offset * (200 / Math.max(1, trialCount - 1)),
    })),
  })
  const trialRefs = (count: number, filename = 'trial.json') =>
    Array.from({ length: count }, (_, offset) => {
      const index = offset + 1
      return {
        index,
        path: `trials/${String(index).padStart(2, '0')}/${filename}`,
        sha256: 'c'.repeat(64),
        status: 'valid',
        trial_id: `trial-${String(index).padStart(2, '0')}`,
      }
    })
  const serviceSelection = (implementation: string) => ({
    build_profile: 'official-gradle-docker-v1',
    environment_profile: 'home-k3s-v1',
    implementation,
    load_profile: 'steady',
    measurement_protocol: 'official-service-v1',
    scenario: 'read-heavy-query-api',
    variant: 'jvm-java25',
  })
  const normalService = {
    cohort_fingerprint: 'service-cohort',
    evidence_family: 'service',
    finished_at: '2026-07-13T10:00:00Z',
    image_digest: 'sha256:service',
    path: 'run-sets/service-cohort/service-run',
    publication_sha256: 'a'.repeat(64),
    run_set_id: 'service-run',
    selection: serviceSelection('java/spring-boot'),
    source_commit: 'b'.repeat(40),
    started_at: '2026-07-13T09:00:00Z',
  }
  const legacyService = {
    cohort_fingerprint: 'service-cohort',
    finished_at: '2026-07-13T10:01:00Z',
    image_digest: 'sha256:legacy',
    path: 'run-sets/service-cohort/legacy-run',
    publication_sha256: 'd'.repeat(64),
    run_set_id: 'legacy-run',
    selection: serviceSelection('java/quarkus'),
    source_commit: 'e'.repeat(40),
    started_at: '2026-07-13T09:01:00Z',
  }
  const lifecycle = {
    cohort_fingerprint: 'lifecycle-cohort',
    evidence_family: 'lifecycle',
    finished_at: '2026-07-13T10:02:00Z',
    image_digest: 'sha256:lifecycle',
    path: 'run-sets/lifecycle-cohort/lifecycle-run',
    publication_sha256: 'f'.repeat(64),
    run_set_id: 'lifecycle-run',
    selection: {
      ...serviceSelection('java/spring-boot'),
      environment_profile: 'home-k3s-lifecycle-v1',
      load_profile: 'none',
      measurement_protocol: 'official-cold-start-v1',
      scenario: 'cold-start-api',
    },
    source_commit: '1'.repeat(40),
    started_at: '2026-07-13T09:02:00Z',
  }
  const build = {
    cohort_fingerprint: 'build-cohort',
    evidence_family: 'build',
    finished_at: '2026-07-13T10:03:00Z',
    path: 'build-run-sets/build-cohort/build-run',
    publication_sha256: '2'.repeat(64),
    run_set_id: 'build-run',
    selection: {
      build_profile: 'official-gradle-docker-v1',
      environment_profile: 'home-build-v1',
      implementation: 'java/spring-boot',
      measurement_protocol: 'official-build-v1',
      variant: 'jvm-java25',
    },
    source_commit: '3'.repeat(40),
    started_at: '2026-07-13T09:03:00Z',
  }
  const standardRunSet = (
    entry: typeof normalService | typeof legacyService,
  ) => ({
    cohort_fingerprint: entry.cohort_fingerprint,
    expected_trials: 1,
    finished_at: entry.finished_at,
    manifest_digest: '4'.repeat(64),
    run_set_id: entry.run_set_id,
    schema_version: '1.0',
    started_at: entry.started_at,
    status: 'complete',
    summary: {
      runtime_metrics: {
        error_rate: metric(0.001, 1),
        p50_ms: metric(1, 1),
        p95_ms: metric(2, 1),
        p99_ms: metric(3, 1),
        rps: metric(300, 1),
      },
      startup_metrics: {},
      trial_count: 1,
      valid_trial_count: 1,
    },
    trials: trialRefs(1),
  })
  const documents = new Map<string, unknown>([
    [
      'catalog.json',
      { entries: [normalService, legacyService, lifecycle, build], schema_version: '1.0' },
    ],
    [`${normalService.path}/run-set.json`, standardRunSet(normalService)],
    [`${legacyService.path}/run-set.json`, standardRunSet(legacyService)],
    [
      `${lifecycle.path}/run-set.json`,
      {
        cohort_fingerprint: lifecycle.cohort_fingerprint,
        expected_trials: 5,
        finished_at: lifecycle.finished_at,
        manifest_digest: '5'.repeat(64),
        run_set_id: lifecycle.run_set_id,
        schema_version: '1.0',
        started_at: lifecycle.started_at,
        status: 'complete',
        summary: {
          runtime_metrics: {},
          startup_metrics: { ready_ms: metric(1_000, 5) },
          trial_count: 5,
          valid_trial_count: 5,
        },
        trials: trialRefs(5),
      },
    ],
    [
      `${build.path}/build-run-set.json`,
      {
        cohort_fingerprint: build.cohort_fingerprint,
        expected_trials: 3,
        run_set_id: build.run_set_id,
        status: 'complete',
        summary: {
          build_metrics: {
            gradle_clean_build_ms: metric(10_000, 3),
            gradle_incremental_rebuild_ms: metric(2_000, 3),
            image_package_ms: metric(4_000, 3),
            image_rebuild_ms: metric(1_000, 3),
          },
          trial_count: 3,
          valid_trial_count: 3,
        },
        trials: trialRefs(3, 'build-trial.json'),
      },
    ],
  ])
  for (const entry of [normalService, legacyService]) {
    const trialDirectory = `${entry.path}/trials/01`
    documents.set(`${trialDirectory}/result.json`, {
      environment: { os_image: 'Ubuntu 24.04' },
      runtime_metrics: {
        cpu_percent_avg: 20,
        cpu_percent_max: 40,
        memory_usage_max_bytes: 256 * 1024 * 1024,
      },
    })
    documents.set(`${trialDirectory}/time-series.json`, {
      sample_interval_ms: 10_000,
      samples: [{
        achieved_rps: 299,
        dependency_cpu_percent: 15,
        dependency_memory_bytes: 128 * 1024 * 1024,
        elapsed_ms: 10_000,
        error_rate: 0.001,
        load_generator_cpu_percent: 10,
        load_generator_memory_bytes: 64 * 1024 * 1024,
        p50_ms: 1,
        p95_ms: 2,
        p99_ms: 3,
        requested_rps: 300,
        target_cpu_percent: 40,
        target_memory_bytes: 256 * 1024 * 1024,
      }],
    })
    documents.set(`${entry.path}/publication.json`, {})
    documents.set(`${entry.path}/resolved-manifest.json`, {
      cohort: { contracts: {} },
    })
  }

  const requested: string[] = []
  let catalogPromise: Promise<void> | null = null
  let releaseCatalog: (() => void) | null = null
  let serviceEvidencePromise: Promise<void> | null = null
  let releaseServiceEvidence: (() => void) | null = null
  let completedDelayedEvidence = 0
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    requested.push(url)
    const path = url.split(`/${revision}/`)[1]
    if (path === 'catalog.json' && catalogPromise) await catalogPromise
    if (hasServiceEvidenceRequest([url]) && serviceEvidencePromise) {
      await serviceEvidencePromise
      completedDelayedEvidence += 1
    }
    const body = documents.get(path)
    return new Response(JSON.stringify(body ?? {}), {
      status: body === undefined ? 404 : 200,
    })
  })

  return {
    delayCatalog() {
      catalogPromise = new Promise((resolve) => {
        releaseCatalog = resolve
      })
    },
    delayServiceEvidence() {
      serviceEvidencePromise = new Promise((resolve) => {
        releaseServiceEvidence = resolve
      })
    },
    fetcher,
    get completedDelayedEvidence() {
      return completedDelayedEvidence
    },
    releaseCatalog() {
      releaseCatalog?.()
      catalogPromise = null
      releaseCatalog = null
    },
    releaseServiceEvidence() {
      releaseServiceEvidence?.()
      serviceEvidencePromise = null
      releaseServiceEvidence = null
    },
    requested,
    reset() {
      requested.length = 0
      fetcher.mockClear()
      catalogPromise = null
      releaseCatalog = null
      serviceEvidencePromise = null
      releaseServiceEvidence = null
      completedDelayedEvidence = 0
    },
    revision,
  }
})

vi.mock('./data/benchmark', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./data/benchmark')>()
  return {
    ...actual,
    createDataSource: (options: { repository: string; revision: string }) =>
      actual.createDataSource({
        ...options,
        fetcher: fixture.fetcher as typeof fetch,
      }),
  }
})

vi.mock('./charts/TimelineChart', () => ({
  TimelineChart: ({ label }: { label: string }) =>
    <div aria-label={label} role="img" />,
}))

import App from './App'

describe('App family views', () => {
  beforeEach(() => {
    fixture.reset()
    window.history.replaceState(null, '', '/')
  })

  afterEach(() => {
    fixture.releaseCatalog()
    fixture.releaseServiceEvidence()
    cleanup()
  })

  it('keeps a family selected while the initial catalog request is pending', async () => {
    fixture.delayCatalog()
    window.history.replaceState(null, '', '/?family=service&cohort=service-cohort')
    render(<App />)

    const buildButton = screen.getByRole('button', { name: 'Build' })
    fireEvent.click(buildButton)
    expect(buildButton.getAttribute('aria-pressed')).toBe('true')
    fixture.releaseCatalog()

    await screen.findByRole('heading', { name: 'Build' })
    expect(buildButton.getAttribute('aria-pressed')).toBe('true')
    expect(new URLSearchParams(window.location.search).get('family')).toBe('build')
    expect(new URLSearchParams(window.location.search).get('cohort')).toBe('build-cohort')
  })

  it('preserves service behavior and isolates evidence fetches after family transitions', async () => {
    render(<App />)

    await screen.findByRole('heading', { name: 'Read Heavy Query Api' })
    expect(screen.getAllByText('Spring Boot').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Quarkus').length).toBeGreaterThan(0)
    expect(screen.getByText('Throughput')).toBeTruthy()
    for (const panel of ['Traffic', 'Latency', 'Errors', 'CPU', 'Memory']) {
      await screen.findByRole('heading', { name: panel })
    }

    expect(fixture.requested).toContain(
      `https://raw.githubusercontent.com/moseoh/hello-realworld-bench/${fixture.revision}/run-sets/service-cohort/legacy-run/run-set.json`,
    )
    expect(fixture.requested).toContain(
      `https://raw.githubusercontent.com/moseoh/hello-realworld-bench/${fixture.revision}/run-sets/service-cohort/service-run/run-set.json`,
    )
    expect(fixture.requested).toContain(
      `https://raw.githubusercontent.com/moseoh/hello-realworld-bench/${fixture.revision}/run-sets/lifecycle-cohort/lifecycle-run/run-set.json`,
    )
    expect(fixture.requested).toContain(
      `https://raw.githubusercontent.com/moseoh/hello-realworld-bench/${fixture.revision}/build-run-sets/build-cohort/build-run/build-run-set.json`,
    )
    expect(fixture.requested.some((url) => url.endsWith('/result.json'))).toBe(true)
    expect(fixture.requested.some((url) => url.endsWith('/time-series.json'))).toBe(true)

    const serviceButton = screen.getByRole('button', { name: 'Service' })
    const lifecycleButton = screen.getByRole('button', { name: 'Cold start' })
    const buildButton = screen.getByRole('button', { name: 'Build' })
    expect(serviceButton.getAttribute('aria-pressed')).toBe('true')
    expect(lifecycleButton.getAttribute('aria-pressed')).toBe('false')
    expect(buildButton.getAttribute('aria-pressed')).toBe('false')

    let requestCount = fixture.requested.length
    fireEvent.click(lifecycleButton)
    await screen.findByRole('heading', { name: 'Cold start' })
    expect(serviceButton.getAttribute('aria-pressed')).toBe('false')
    expect(lifecycleButton.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('Ready Ms')).toBeTruthy()
    expect(hasServiceEvidenceRequest(fixture.requested.slice(requestCount))).toBe(false)

    requestCount = fixture.requested.length
    fireEvent.click(buildButton)
    await screen.findByRole('heading', { name: 'Build' })
    expect(lifecycleButton.getAttribute('aria-pressed')).toBe('false')
    expect(buildButton.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('Gradle clean build')).toBeTruthy()
    expect(screen.getByText('Gradle incremental rebuild')).toBeTruthy()
    expect(screen.getByText('Image package')).toBeTruthy()
    expect(screen.getByText('Image rebuild')).toBeTruthy()
    await waitFor(() => {
      expect(hasServiceEvidenceRequest(fixture.requested.slice(requestCount))).toBe(false)
    })
  })

  it('ignores service evidence that completes after entering a non-service family', async () => {
    fixture.delayServiceEvidence()
    render(<App />)

    await screen.findByRole('heading', { name: 'Read Heavy Query Api' })
    await waitFor(() => {
      expect(fixture.requested.some((url) => url.endsWith('/time-series.json'))).toBe(true)
    })
    const requestCount = fixture.requested.length

    fireEvent.click(screen.getByRole('button', { name: 'Cold start' }))
    await screen.findByRole('heading', { name: 'Cold start' })
    fixture.releaseServiceEvidence()
    await waitFor(() => {
      expect(fixture.completedDelayedEvidence).toBeGreaterThan(0)
    })

    expect(screen.queryByRole('heading', { name: 'Traffic' })).toBeNull()
    expect(screen.getByRole('heading', { name: 'Cold start' })).toBeTruthy()
    expect(hasServiceEvidenceRequest(fixture.requested.slice(requestCount))).toBe(false)
  })
})

function hasServiceEvidenceRequest(urls: string[]): boolean {
  return urls.some(
    (url) => url.endsWith('/result.json') || url.endsWith('/time-series.json'),
  )
}
