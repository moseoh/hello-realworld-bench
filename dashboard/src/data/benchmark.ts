export interface Selection {
  build_profile: string
  environment_profile: string
  implementation: string
  load_profile: string
  measurement_protocol: string
  scenario: string
  variant: string
}

export interface CatalogEntry {
  cohort_fingerprint: string
  finished_at: string
  image_digest: string
  path: string
  publication_sha256: string
  run_set_id: string
  selection: Selection
  source_commit: string
  started_at: string
}

export interface MetricSummary {
  min: number
  median: number
  max: number
  trials: Array<{ trial_id: string; value: number }>
}

export interface RunSet {
  cohort_fingerprint: string
  expected_trials: number
  finished_at: string
  manifest_digest: string
  run_set_id: string
  schema_version: string
  started_at: string
  status: string
  summary: {
    trial_count: number
    valid_trial_count: number
    runtime_metrics: Record<string, MetricSummary>
    startup_metrics: Record<string, MetricSummary>
  }
  trials: Array<{
    index: number
    path: string
    sha256: string
    status: string
    trial_id: string
  }>
}

export interface ComparisonSelection {
  cohort: string
  loadProfile: string
  scenario: string
}

export interface ComparisonItem {
  entry: CatalogEntry
  runSet: RunSet
}

export interface TimelineSample {
  elapsed_ms: number
  requested_rps?: number
  achieved_rps?: number
  request_count?: number
  failure_count?: number
  error_rate?: number
  p50_ms?: number
  p95_ms?: number
  p99_ms?: number
  target_cpu_percent?: number
  target_memory_bytes?: number
  dependency_cpu_percent?: number
  dependency_memory_bytes?: number
  load_generator_cpu_percent?: number
  load_generator_memory_bytes?: number
}

export interface NormalizedTimeline {
  sampleIntervalMs?: number
  samples: TimelineSample[]
}

export interface DataSource {
  catalog: () => Promise<CatalogEntry[]>
  document: (path: string) => Promise<unknown>
  runSet: (path: string) => Promise<RunSet>
}

interface DataSourceOptions {
  fetcher?: typeof fetch
  repository: string
  revision: string
}

const TIMELINE_FIELDS: Array<keyof Omit<TimelineSample, 'elapsed_ms'>> = [
  'requested_rps',
  'achieved_rps',
  'request_count',
  'failure_count',
  'error_rate',
  'p50_ms',
  'p95_ms',
  'p99_ms',
  'target_cpu_percent',
  'target_memory_bytes',
  'dependency_cpu_percent',
  'dependency_memory_bytes',
  'load_generator_cpu_percent',
  'load_generator_memory_bytes',
]

export function createDataSource({
  fetcher = fetch,
  repository,
  revision,
}: DataSourceOptions): DataSource {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new Error(`Invalid repository: ${repository}`)
  }
  if (!/^[A-Za-z0-9_.-]+$/.test(revision)) {
    throw new Error(`Invalid data revision: ${revision}`)
  }

  const baseUrl = `https://raw.githubusercontent.com/${repository}/${revision}`
  const document = async (path: string): Promise<unknown> => {
    assertSafePath(path)
    const response = await fetcher(`${baseUrl}/${path}`)
    if (!response.ok) {
      throw new Error(`Dataset request failed (${response.status}): ${path}`)
    }
    return response.json()
  }

  return {
    async catalog() {
      const raw = await document('catalog.json')
      if (!isRecord(raw) || !Array.isArray(raw.entries)) {
        throw new Error('Invalid benchmark catalog')
      }
      return raw.entries as CatalogEntry[]
    },
    document,
    async runSet(path: string) {
      const raw = await document(`${path}/run-set.json`)
      if (!isRecord(raw) || typeof raw.run_set_id !== 'string') {
        throw new Error(`Invalid run set: ${path}`)
      }
      return raw as unknown as RunSet
    },
  }
}

export function buildComparison(
  entries: CatalogEntry[],
  runSets: ReadonlyMap<string, RunSet>,
  selection: ComparisonSelection,
): ComparisonItem[] {
  const latestByTarget = new Map<string, ComparisonItem>()

  for (const entry of entries) {
    if (
      entry.cohort_fingerprint !== selection.cohort ||
      entry.selection.load_profile !== selection.loadProfile ||
      entry.selection.scenario !== selection.scenario
    ) {
      continue
    }

    const runSet = runSets.get(entry.run_set_id)
    if (!runSet || !isCompleteRunSet(runSet, entry.cohort_fingerprint)) {
      continue
    }

    const target = comparisonItemKey(entry)
    const existing = latestByTarget.get(target)
    if (!existing || existing.entry.finished_at < entry.finished_at) {
      latestByTarget.set(target, { entry, runSet })
    }
  }

  return [...latestByTarget.values()].sort(
    (left, right) =>
      left.entry.selection.implementation.localeCompare(
        right.entry.selection.implementation,
      ) || left.entry.selection.variant.localeCompare(right.entry.selection.variant),
  )
}

export function comparisonItemKey(entry: CatalogEntry): string {
  return `${entry.selection.implementation}\0${entry.selection.variant}`
}

export function normalizeTimeline(document: unknown): NormalizedTimeline {
  if (!isRecord(document) || !Array.isArray(document.samples)) {
    return { samples: [] }
  }

  const samples = document.samples
    .filter(isRecord)
    .flatMap((raw) => {
      const elapsed = raw.elapsed_ms
      if (!isFiniteNumber(elapsed) || elapsed < 0) {
        return []
      }

      const sample: TimelineSample = { elapsed_ms: elapsed }
      for (const field of TIMELINE_FIELDS) {
        const value = raw[field]
        if (isFiniteNumber(value)) {
          sample[field] = value
        }
      }
      return [sample]
    })
    .sort((left, right) => left.elapsed_ms - right.elapsed_ms)
  const interval = document.sample_interval_ms
  return {
    sampleIntervalMs:
      isFiniteNumber(interval) && interval > 0 ? interval : undefined,
    samples,
  }
}

export function isCompleteRunSet(runSet: RunSet, cohort: string): boolean {
  return (
    runSet.cohort_fingerprint === cohort &&
    runSet.status === 'complete' &&
    runSet.expected_trials > 0 &&
    runSet.summary.trial_count === runSet.expected_trials &&
    runSet.summary.valid_trial_count === runSet.expected_trials &&
    runSet.trials.length === runSet.expected_trials &&
    runSet.trials.every((trial) => trial.status === 'valid')
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function assertSafePath(path: string): void {
  if (
    !path ||
    path.startsWith('/') ||
    path.includes('\\') ||
    path.split('/').some((segment) => !segment || segment === '.' || segment === '..')
  ) {
    throw new Error(`Unsafe dataset path: ${path}`)
  }
}
