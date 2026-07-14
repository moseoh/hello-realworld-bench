import {
  buildComparison,
  type CatalogEntry,
  comparisonItemKey,
  type ComparisonItem,
  type BuildCatalogEntry,
  type BuildRunSet,
  type EvidenceRunSet,
  isBuildCatalogEntry,
  isCompleteBuildRunSet,
  isCompleteLifecycleRunSet,
  isLifecycleCatalogEntry,
  isServiceCatalogEntry,
  type LifecycleCatalogEntry,
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

export interface LifecycleComparisonItem {
  entry: LifecycleCatalogEntry
  runSet: RunSet
}

export interface LifecycleGroup {
  cohort: string
  implementationCount: number
  items: LifecycleComparisonItem[]
  latestAt: string
}

export interface BuildComparisonItem {
  entry: BuildCatalogEntry
  runSet: BuildRunSet
}

export interface BuildGroup {
  cohort: string
  implementationCount: number
  items: BuildComparisonItem[]
  latestAt: string
}

export function listComparisonGroups(
  entries: CatalogEntry[],
  runSets: ReadonlyMap<string, EvidenceRunSet>,
): ComparisonGroup[] {
  const keys = new Map<
    string,
    { cohort: string; loadProfile: string; scenario: string }
  >()

  for (const entry of entries.filter(isServiceCatalogEntry)) {
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

export function listLifecycleGroups(
  entries: CatalogEntry[],
  runSets: ReadonlyMap<string, EvidenceRunSet>,
): LifecycleGroup[] {
  const groups = new Map<string, LifecycleComparisonItem[]>()
  for (const entry of entries) {
    if (!isLifecycleCatalogEntry(entry)) continue
    const runSet = runSets.get(entry.run_set_id)
    if (!runSet || !isCompleteLifecycleRunSet(runSet, entry.cohort_fingerprint)) continue
    const items = groups.get(entry.cohort_fingerprint) ?? []
    upsertLatest(items, { entry, runSet })
    groups.set(entry.cohort_fingerprint, items)
  }
  return familyGroups(groups)
}

export function listBuildGroups(
  entries: CatalogEntry[],
  runSets: ReadonlyMap<string, EvidenceRunSet>,
): BuildGroup[] {
  const groups = new Map<string, BuildComparisonItem[]>()
  for (const entry of entries) {
    if (!isBuildCatalogEntry(entry)) continue
    const runSet = runSets.get(entry.run_set_id)
    if (!runSet || !isCompleteBuildRunSet(runSet, entry.cohort_fingerprint)) continue
    const items = groups.get(entry.cohort_fingerprint) ?? []
    upsertLatest(items, { entry, runSet })
    groups.set(entry.cohort_fingerprint, items)
  }
  return familyGroups(groups)
}

export function selectFamilyGroup<T extends { cohort: string }>(
  groups: T[],
  cohort: string,
): T | null {
  return groups.find((group) => group.cohort === cohort) ?? groups[0] ?? null
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

function upsertLatest<T extends { entry: CatalogEntry }>(
  items: T[],
  item: T,
): void {
  const key = comparisonItemKey(item.entry)
  const index = items.findIndex((candidate) => comparisonItemKey(candidate.entry) === key)
  if (index === -1) {
    items.push(item)
  } else if (items[index].entry.finished_at < item.entry.finished_at) {
    items[index] = item
  }
}

function familyGroups<T extends { entry: CatalogEntry }>(
  groups: Map<string, T[]>,
): Array<{ cohort: string; implementationCount: number; items: T[]; latestAt: string }> {
  return [...groups.entries()]
    .map(([cohort, items]) => ({
      cohort,
      implementationCount: items.length,
      items: items.sort(
        (left, right) =>
          left.entry.selection.implementation.localeCompare(
            right.entry.selection.implementation,
          ) || left.entry.selection.variant.localeCompare(right.entry.selection.variant),
      ),
      latestAt: items.reduce(
        (latest, item) =>
          item.entry.finished_at > latest ? item.entry.finished_at : latest,
        '',
      ),
    }))
    .sort(
      (left, right) =>
        right.implementationCount - left.implementationCount ||
        right.latestAt.localeCompare(left.latestAt),
    )
}
