export interface BaseSelection {
  build_profile: string
  environment_profile: string
  implementation: string
  measurement_protocol: string
  variant: string
}

export interface ServiceSelection extends BaseSelection {
  load_profile: string
  scenario: string
}

export interface BuildSelection extends BaseSelection {}

interface StandardCatalogEntry {
  cohort_fingerprint: string
  finished_at: string
  image_digest: string
  path: string
  publication_sha256: string
  run_set_id: string
  selection: ServiceSelection
  source_commit: string
  started_at: string
}

export interface ServiceCatalogEntry extends StandardCatalogEntry {
  evidence_family?: 'service'
}

export interface LifecycleCatalogEntry extends StandardCatalogEntry {
  evidence_family: 'lifecycle'
}

export interface BuildCatalogEntry {
  cohort_fingerprint: string
  evidence_family: 'build'
  finished_at: string
  path: string
  publication_sha256: string
  run_set_id: string
  selection: BuildSelection
  source_commit: string
  started_at: string
}

export type CatalogEntry =
  | ServiceCatalogEntry
  | LifecycleCatalogEntry
  | BuildCatalogEntry

export interface MetricSummary {
  min: number
  median: number
  max: number
  trials: Array<{ trial_id: string; value: number }>
}

export const BUILD_METRIC_KEYS = [
  'gradle_clean_build_ms',
  'gradle_incremental_rebuild_ms',
  'image_package_ms',
  'image_rebuild_ms',
] as const

export type BuildMetricKey = (typeof BUILD_METRIC_KEYS)[number]
export type BuildMetrics = Record<BuildMetricKey, MetricSummary>

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

export interface BuildRunSet {
  cohort_fingerprint: string
  expected_trials: number
  manifest_digest?: string
  run_set_id: string
  schema_version?: string
  status: string
  summary: {
    build_metrics: BuildMetrics
    trial_count: number
    valid_trial_count: number
  }
  trials: Array<{
    index: number
    path: string
    sha256: string
    status: string
    trial_id: string
  }>
}

export type EvidenceRunSet = RunSet | BuildRunSet

export interface ComparisonSelection {
  cohort: string
  loadProfile: string
  scenario: string
}

export interface ComparisonItem {
  entry: ServiceCatalogEntry
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
  runSet: (entry: CatalogEntry) => Promise<EvidenceRunSet>
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
  const base = new URL(`${baseUrl}/`)
  const document = async (path: string): Promise<unknown> => {
    assertSafePath(path)
    const url = new URL(path, base)
    if (
      url.origin !== base.origin ||
      !url.pathname.startsWith(base.pathname) ||
      url.search !== '' ||
      url.hash !== ''
    ) {
      throw new Error(`Unsafe dataset URL: ${path}`)
    }
    const response = await fetcher(url.toString())
    if (!response.ok) {
      throw new Error(`Dataset request failed (${response.status}): ${path}`)
    }
    return response.json()
  }

  return {
    async catalog() {
      const raw = await document('catalog.json')
      if (
        !isRecord(raw) ||
        raw.schema_version !== '1.0' ||
        !Array.isArray(raw.entries)
      ) {
        throw new Error('Invalid benchmark catalog')
      }
      return raw.entries.filter(isCatalogEntry)
    },
    document,
    async runSet(entry: CatalogEntry) {
      const filename = isBuildCatalogEntry(entry) ? 'build-run-set.json' : 'run-set.json'
      const raw = await document(`${entry.path}/${filename}`)
      if (!isRunSetForCatalogEntry(raw, entry)) {
        throw new Error(`Run set does not match catalog entry: ${entry.path}`)
      }
      return raw
    },
  }
}

export function buildComparison(
  entries: CatalogEntry[],
  runSets: ReadonlyMap<string, EvidenceRunSet>,
  selection: ComparisonSelection,
): ComparisonItem[] {
  const latestByTarget = new Map<string, ComparisonItem>()

  for (const entry of entries) {
    if (
      !isServiceCatalogEntry(entry) ||
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

export function isCompleteRunSet(
  runSet: EvidenceRunSet,
  cohort: string,
): runSet is RunSet {
  return (
    !isBuildRunSet(runSet) &&
    runSet.cohort_fingerprint === cohort &&
    runSet.status === 'complete' &&
    runSet.expected_trials > 0 &&
    runSet.summary.trial_count === runSet.expected_trials &&
    runSet.summary.valid_trial_count === runSet.expected_trials &&
    runSet.trials.length === runSet.expected_trials &&
    runSet.trials.every((trial) => trial.status === 'valid')
  )
}

export function isCompleteBuildRunSet(
  runSet: EvidenceRunSet,
  cohort: string,
): runSet is BuildRunSet {
  return (
    isBuildRunSet(runSet) &&
    runSet.cohort_fingerprint === cohort &&
    runSet.status === 'complete' &&
    runSet.expected_trials === 3 &&
    runSet.summary.trial_count === 3 &&
    runSet.summary.valid_trial_count === 3 &&
    isExactTrialReferences(runSet.trials, 3, 'build-trial.json') &&
    isBuildMetricGroup(runSet.summary.build_metrics, 3)
  )
}

export function isCompleteLifecycleRunSet(
  runSet: EvidenceRunSet,
  cohort: string,
): runSet is RunSet {
  return (
    isCompleteRunSet(runSet, cohort) &&
    isMetricGroup(runSet.summary.startup_metrics, runSet.expected_trials)
  )
}

export function isServiceCatalogEntry(
  entry: CatalogEntry,
): entry is ServiceCatalogEntry {
  return (
    entry.evidence_family === 'service' ||
    (entry.evidence_family === undefined &&
      entry.selection.measurement_protocol === 'official-service-v1')
  )
}

export function isLifecycleCatalogEntry(
  entry: CatalogEntry,
): entry is LifecycleCatalogEntry {
  return entry.evidence_family === 'lifecycle'
}

export function isBuildCatalogEntry(
  entry: CatalogEntry,
): entry is BuildCatalogEntry {
  return entry.evidence_family === 'build'
}

function isBuildRunSet(runSet: unknown): runSet is BuildRunSet {
  return (
    isRecord(runSet) &&
    typeof runSet.cohort_fingerprint === 'string' &&
    Number.isInteger(runSet.expected_trials) &&
    typeof runSet.run_set_id === 'string' &&
    typeof runSet.status === 'string' &&
    Array.isArray(runSet.trials) &&
    isRecord(runSet.summary) &&
    'build_metrics' in runSet.summary &&
    Number.isInteger(runSet.summary.trial_count) &&
    Number.isInteger(runSet.summary.valid_trial_count)
  )
}

function isBuildMetricGroup(value: unknown, expectedTrials: number): value is BuildMetrics {
  if (!isRecord(value) || Object.keys(value).length !== BUILD_METRIC_KEYS.length) {
    return false
  }
  return BUILD_METRIC_KEYS.every((key) =>
    isMetricSummary(value[key], expectedTrials),
  )
}

function isMetricGroup(
  value: unknown,
  expectedTrials: number,
): value is Record<string, MetricSummary> {
  return (
    isRecord(value) &&
    Object.keys(value).length > 0 &&
    Object.values(value).every((metric) =>
      isMetricSummary(metric, expectedTrials),
    )
  )
}

function isMetricSummary(value: unknown, expectedTrials: number): value is MetricSummary {
  if (
    !isRecord(value) ||
    !isFiniteNumber(value.min) ||
    !isFiniteNumber(value.median) ||
    !isFiniteNumber(value.max) ||
    value.min < 0 ||
    value.median < 0 ||
    value.max < 0 ||
    !Array.isArray(value.trials) ||
    value.trials.length !== expectedTrials
  ) {
    return false
  }
  const trialValues: number[] = []
  for (let index = 0; index < expectedTrials; index += 1) {
    const trial = value.trials[index]
    if (
      !isRecord(trial) ||
      trial.trial_id !== `trial-${String(index + 1).padStart(2, '0')}` ||
      !isFiniteNumber(trial.value) ||
      trial.value < 0
    ) {
      return false
    }
    trialValues.push(trial.value)
  }
  const sorted = [...trialValues].sort((left, right) => left - right)
  const middle = Math.floor(sorted.length / 2)
  const recomputedMedian = sorted.length % 2 === 1
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2
  return (
    value.min === sorted[0] &&
    value.median === recomputedMedian &&
    value.max === sorted[sorted.length - 1]
  )
}

function isExactTrialReferences(
  value: unknown,
  expectedTrials: number,
  filename: string,
): boolean {
  if (!Array.isArray(value) || value.length !== expectedTrials) return false
  return value.every((trial, offset) => {
    const index = offset + 1
    const directory = String(index).padStart(2, '0')
    return (
      isRecord(trial) &&
      trial.index === index &&
      trial.trial_id === `trial-${directory}` &&
      trial.path === `trials/${directory}/${filename}` &&
      typeof trial.sha256 === 'string' &&
      trial.status === 'valid'
    )
  })
}

function isCatalogEntry(value: unknown): value is CatalogEntry {
  if (!isRecord(value) || !isRecord(value.selection)) return false
  const commonStrings = [
    value.cohort_fingerprint,
    value.finished_at,
    value.path,
    value.publication_sha256,
    value.run_set_id,
    value.source_commit,
    value.started_at,
    value.selection.build_profile,
    value.selection.environment_profile,
    value.selection.implementation,
    value.selection.measurement_protocol,
    value.selection.variant,
  ]
  if (commonStrings.some((field) => typeof field !== 'string' || field.length === 0)) {
    return false
  }
  if (
    !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(String(value.cohort_fingerprint)) ||
    !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(String(value.run_set_id))
  ) {
    return false
  }
  try {
    assertSafePath(String(value.path))
  } catch {
    return false
  }
  const family = value.evidence_family
  if (family === 'build') {
    return value.path === `build-run-sets/${value.cohort_fingerprint}/${value.run_set_id}`
  }
  if (family !== undefined && family !== 'service' && family !== 'lifecycle') {
    return false
  }
  if (
    typeof value.image_digest !== 'string' ||
    typeof value.selection.load_profile !== 'string' ||
    typeof value.selection.scenario !== 'string'
  ) {
    return false
  }
  if (
    family === undefined &&
    value.selection.measurement_protocol !== 'official-service-v1'
  ) {
    return false
  }
  return value.path === `run-sets/${value.cohort_fingerprint}/${value.run_set_id}`
}

function isRunSetForCatalogEntry(
  value: unknown,
  entry: CatalogEntry,
): value is EvidenceRunSet {
  if (!isRecord(value)) return false
  if (
    value.run_set_id !== entry.run_set_id ||
    value.cohort_fingerprint !== entry.cohort_fingerprint
  ) {
    return false
  }
  if (isBuildCatalogEntry(entry)) return isBuildRunSet(value)
  return (
    typeof value.status === 'string' &&
    Number.isInteger(value.expected_trials) &&
    Array.isArray(value.trials) &&
    isRecord(value.summary) &&
    isRecord(value.summary.runtime_metrics) &&
    isRecord(value.summary.startup_metrics)
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
    path.split('/').some(
      (segment) =>
        !segment ||
        segment === '.' ||
        segment === '..' ||
        !/^[A-Za-z0-9._-]+$/.test(segment),
    )
  ) {
    throw new Error(`Unsafe dataset path: ${path}`)
  }
}
