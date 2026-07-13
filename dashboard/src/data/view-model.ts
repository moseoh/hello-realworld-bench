import {
  buildComparison,
  type CatalogEntry,
  type ComparisonItem,
  type RunSet,
} from './benchmark'

export interface ComparisonGroup {
  cohort: string
  implementationCount: number
  items: ComparisonItem[]
  latestAt: string
  loadProfile: string
  scenario: string
}

export interface ResourceSummary {
  cpuAveragePercent?: number
  cpuMaxPercent?: number
  memoryMaxBytes?: number
}

export interface GroupSelection {
  cohort: string
  loadProfile: string
  scenario: string
}

export function listComparisonGroups(
  entries: CatalogEntry[],
  runSets: ReadonlyMap<string, RunSet>,
): ComparisonGroup[] {
  const keys = new Map<
    string,
    { cohort: string; loadProfile: string; scenario: string }
  >()

  for (const entry of entries.filter(
    (candidate) =>
      candidate.evidence_family === 'service' ||
      (candidate.evidence_family === undefined &&
        candidate.selection.measurement_protocol === 'official-service-v1'),
  )) {
    const value = {
      cohort: entry.cohort_fingerprint,
      loadProfile: entry.selection.load_profile,
      scenario: entry.selection.scenario,
    }
    keys.set(`${value.scenario}\0${value.loadProfile}\0${value.cohort}`, value)
  }

  return [...keys.values()]
    .map((selection) => {
      const items = buildComparison(entries, runSets, selection)
      return {
        ...selection,
        implementationCount: items.length,
        items,
        latestAt: items.reduce(
          (latest, item) =>
            item.entry.finished_at > latest ? item.entry.finished_at : latest,
          '',
        ),
      }
    })
    .filter((group) => group.items.length > 0)
    .sort(
      (left, right) =>
        right.implementationCount - left.implementationCount ||
        right.latestAt.localeCompare(left.latestAt) ||
        left.scenario.localeCompare(right.scenario),
    )
}

export function summarizeTrialResources(results: unknown[]): ResourceSummary {
  return {
    cpuAveragePercent: medianMetric(results, 'cpu_percent_avg'),
    cpuMaxPercent: medianMetric(results, 'cpu_percent_max'),
    memoryMaxBytes: medianMetric(results, 'memory_usage_max_bytes'),
  }
}

export function selectComparisonGroup(
  groups: ComparisonGroup[],
  selection: GroupSelection,
): ComparisonGroup | null {
  const candidates = groups.filter(
    (group) =>
      group.scenario === selection.scenario &&
      group.loadProfile === selection.loadProfile,
  )
  if (selection.cohort) {
    return candidates.find((group) => group.cohort === selection.cohort) ?? null
  }
  return candidates[0] ?? null
}

function medianMetric(results: unknown[], metric: string): number | undefined {
  const values = results
    .flatMap((result) => {
      if (!isRecord(result) || !isRecord(result.runtime_metrics)) {
        return []
      }
      const value = result.runtime_metrics[metric]
      return typeof value === 'number' && Number.isFinite(value) ? [value] : []
    })
    .sort((left, right) => left - right)

  if (values.length === 0) {
    return undefined
  }
  const middle = Math.floor(values.length / 2)
  return values.length % 2 === 1
    ? values[middle]
    : (values[middle - 1] + values[middle]) / 2
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
