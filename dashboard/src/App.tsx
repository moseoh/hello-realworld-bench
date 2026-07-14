import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  Database,
  ExternalLink,
  GitBranch,
  Server,
} from 'lucide-react'

import './App.css'
import {
  errorOption,
  latencyOption,
  memoryOption,
  resourceOption,
  trafficOption,
} from './charts/options'
import {
  createDataSource,
  comparisonItemKey,
  normalizeTimeline,
  type CatalogEntry,
  type ComparisonItem,
  type EvidenceRunSet,
  type TimelineSample,
} from './data/benchmark'
import {
  listBuildGroups,
  listComparisonGroups,
  listLifecycleGroups,
  selectFamilyGroup,
  selectComparisonGroup,
  summarizeTrialResources,
  type BuildGroup,
  type ComparisonGroup,
  type LifecycleGroup,
} from './data/view-model'

const REPOSITORY =
  import.meta.env.VITE_DATA_REPOSITORY ?? 'moseoh/hello-realworld-bench'
const DATA_REVISION = import.meta.env.VITE_DATA_COMMIT ?? 'benchmark-data'

interface LoadedDataset {
  buildGroups: BuildGroup[]
  entries: CatalogEntry[]
  groups: ComparisonGroup[]
  lifecycleGroups: LifecycleGroup[]
  runSets: Map<string, EvidenceRunSet>
}

type EvidenceFamily = 'service' | 'lifecycle' | 'build'

interface TrialDetail {
  manifest: unknown
  publication: unknown
  result: unknown
  sampleIntervalMs?: number
  samples: TimelineSample[]
}

const source = createDataSource({
  repository: REPOSITORY,
  revision: DATA_REVISION,
})

const TimelineChart = lazy(() =>
  import('./charts/TimelineChart').then((module) => ({
    default: module.TimelineChart,
  })),
)

function App() {
  const [dataset, setDataset] = useState<LoadedDataset | null>(null)
  const [datasetError, setDatasetError] = useState<string | null>(null)
  const [evidenceError, setEvidenceError] = useState<string | null>(null)
  const [family, setFamily] = useState<EvidenceFamily>(() => familyFromSearch())
  const [familyCohort, setFamilyCohort] = useState('')
  const [scenario, setScenario] = useState('')
  const [loadProfile, setLoadProfile] = useState('')
  const [cohort, setCohort] = useState('')
  const [selectedTarget, setSelectedTarget] = useState('')
  const [selectedTrial, setSelectedTrial] = useState(1)
  const [results, setResults] = useState<Map<string, unknown[]>>(new Map())
  const [detail, setDetail] = useState<TrialDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const entries = await source.catalog()
        const loaded = await Promise.all(
          entries.map(async (entry) => [entry.run_set_id, await source.runSet(entry)] as const),
        )
        if (cancelled) return
        const runSets = new Map(loaded)
        const groups = listComparisonGroups(entries, runSets)
        const lifecycleGroups = listLifecycleGroups(entries, runSets)
        const buildGroups = listBuildGroups(entries, runSets)
        setDataset({ buildGroups, entries, groups, lifecycleGroups, runSets })
        const query = new URLSearchParams(window.location.search)
        const requestedFamily = familyFromSearch()
        const requestedScenario = query.get('scenario')
        const requestedProfile = query.get('profile')
        const requestedCohort = query.get('cohort') ?? ''
        const initial =
          selectComparisonGroup(groups, {
            cohort: requestedCohort,
            loadProfile: requestedProfile ?? '',
            scenario: requestedScenario ?? '',
          }) ??
          selectComparisonGroup(groups, {
            cohort: '',
            loadProfile: requestedProfile ?? '',
            scenario: requestedScenario ?? '',
          }) ??
          groups[0]
        if (initial) {
          setScenario(initial.scenario)
          setLoadProfile(initial.loadProfile)
          setCohort(initial.cohort)
        }
        const requestedFamilyCohort = query.get('cohort') ?? ''
        const familyInitial = requestedFamily === 'lifecycle'
          ? selectFamilyGroup(lifecycleGroups, requestedFamilyCohort)
          : selectFamilyGroup(buildGroups, requestedFamilyCohort)
        if (familyInitial) setFamilyCohort(familyInitial.cohort)
      } catch (error) {
        if (!cancelled) {
          setDatasetError(error instanceof Error ? error.message : String(error))
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const scenarioOptions = useMemo(
    () => unique(dataset?.groups.map((group) => group.scenario) ?? []),
    [dataset],
  )
  const profileOptions = useMemo(
    () =>
      unique(
        dataset?.groups
          .filter((group) => group.scenario === scenario)
          .map((group) => group.loadProfile) ?? [],
      ),
    [dataset, scenario],
  )
  const cohortOptions = useMemo(
    () =>
      dataset?.groups.filter(
        (candidate) =>
          candidate.scenario === scenario &&
          candidate.loadProfile === loadProfile,
      ) ?? [],
    [dataset, loadProfile, scenario],
  )
  const group = useMemo(
    () =>
      selectComparisonGroup(dataset?.groups ?? [], {
        cohort,
        loadProfile,
        scenario,
      }),
    [cohort, dataset, loadProfile, scenario],
  )
  const lifecycleGroup = useMemo(
    () => selectFamilyGroup(dataset?.lifecycleGroups ?? [], familyCohort),
    [dataset, familyCohort],
  )
  const buildGroup = useMemo(
    () => selectFamilyGroup(dataset?.buildGroups ?? [], familyCohort),
    [dataset, familyCohort],
  )
  const activeFamilyGroup = family === 'lifecycle' ? lifecycleGroup : buildGroup

  useEffect(() => {
    if (profileOptions.length > 0 && !profileOptions.includes(loadProfile)) {
      setLoadProfile(profileOptions[0])
    }
  }, [loadProfile, profileOptions])

  useEffect(() => {
    if (cohortOptions.length > 0 && !cohortOptions.some((item) => item.cohort === cohort)) {
      setCohort(cohortOptions[0].cohort)
    }
  }, [cohort, cohortOptions])

  useEffect(() => {
    if (family !== 'service' || !group) return
    const query = new URLSearchParams(window.location.search)
    query.set('family', family)
    query.set('scenario', group.scenario)
    query.set('profile', group.loadProfile)
    query.set('cohort', group.cohort)
    window.history.replaceState(null, '', `${window.location.pathname}?${query}`)
    setSelectedTarget((current) =>
      group.items.some((item) => comparisonItemKey(item.entry) === current)
        ? current
        : group.items[0] ? comparisonItemKey(group.items[0].entry) : '',
    )
    setSelectedTrial(1)

    let cancelled = false
    setEvidenceError(null)
    setResults(new Map())
    async function loadResults() {
      const loaded = await Promise.all(
        group!.items.map(async (item) => {
          const trialResults = await Promise.all(
            item.runSet.trials.map((trial) =>
              source.document(`${item.entry.path}/${trialDirectory(trial.path)}/result.json`),
            ),
          )
          return [item.entry.run_set_id, trialResults] as const
        }),
      )
      if (!cancelled) setResults(new Map(loaded))
    }
    void loadResults().catch((error) => {
      if (!cancelled) setEvidenceError(error instanceof Error ? error.message : String(error))
    })
    return () => {
      cancelled = true
    }
  }, [family, group])

  useEffect(() => {
    if (family === 'service' || !activeFamilyGroup) return
    const query = new URLSearchParams(window.location.search)
    query.set('family', family)
    query.set('cohort', activeFamilyGroup.cohort)
    window.history.replaceState(null, '', `${window.location.pathname}?${query}`)
    setFamilyCohort(activeFamilyGroup.cohort)
  }, [activeFamilyGroup, family])

  const selectedItem = group?.items.find(
    (item) => comparisonItemKey(item.entry) === selectedTarget,
  )

  function selectEvidenceFamily(nextFamily: EvidenceFamily) {
    setFamily(nextFamily)
    const query = new URLSearchParams(window.location.search)
    query.set('family', nextFamily)
    window.history.replaceState(null, '', `${window.location.pathname}?${query}`)
  }

  useEffect(() => {
    if (family !== 'service' || !selectedItem) return
    const trial = selectedItem.runSet.trials.find((candidate) => candidate.index === selectedTrial)
    if (!trial) return
    let cancelled = false
    setEvidenceError(null)
    setDetail(null)
    setDetailLoading(true)
    const directory = `${selectedItem.entry.path}/${trialDirectory(trial.path)}`
    Promise.all([
      source.document(`${directory}/result.json`),
      source.document(`${directory}/time-series.json`),
      source.document(`${selectedItem.entry.path}/publication.json`),
      source.document(`${selectedItem.entry.path}/resolved-manifest.json`),
    ])
      .then(([result, timeline, publication, manifest]) => {
        if (!cancelled) {
          const normalized = normalizeTimeline(timeline)
          setDetail({
            manifest,
            publication,
            result,
            sampleIntervalMs: normalized.sampleIntervalMs,
            samples: normalized.samples,
          })
        }
      })
      .catch((error) => {
        if (!cancelled) setEvidenceError(error instanceof Error ? error.message : String(error))
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [family, selectedItem, selectedTrial])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true"><Activity size={18} /></div>
        <div className="brand-copy">
          <h1>Hello Real World Bench</h1>
          <p>Backend runtime benchmarks beyond Hello World.</p>
        </div>
        <div className="topbar-meta">
          <span className="status-chip"><CheckCircle2 size={14} />Experimental</span>
          <a
            aria-label="Open source repository"
            className="icon-link"
            href={`https://github.com/${REPOSITORY}`}
            rel="noreferrer"
            target="_blank"
            title="Open source repository"
          >
            <GitBranch size={18} />
          </a>
        </div>
      </header>

      <main>
        <section className="control-band" aria-label="Benchmark selection">
          <div className="control-heading">
            <span className="eyebrow">Official dataset</span>
            <strong>{dataset ? `${dataset.entries.length} published run sets` : 'Loading published runs'}</strong>
          </div>
          <div className="family-tabs" aria-label="Evidence family">
            <FamilyButton active={family === 'service'} onClick={() => selectEvidenceFamily('service')}>Service</FamilyButton>
            <FamilyButton active={family === 'lifecycle'} onClick={() => selectEvidenceFamily('lifecycle')}>Cold start</FamilyButton>
            <FamilyButton active={family === 'build'} onClick={() => selectEvidenceFamily('build')}>Build</FamilyButton>
          </div>
          {family === 'service' ? <>
            <label>
              <span>Scenario</span>
              <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
                {scenarioOptions.map((value) => <option key={value} value={value}>{label(value)}</option>)}
              </select>
            </label>
            <label>
              <span>Traffic profile</span>
              <select value={loadProfile} onChange={(event) => setLoadProfile(event.target.value)}>
                {profileOptions.map((value) => <option key={value} value={value}>{label(value)}</option>)}
              </select>
            </label>
            <label>
              <span>Contract cohort</span>
              <select value={cohort} onChange={(event) => setCohort(event.target.value)}>
                {cohortOptions.map((value) => (
                  <option key={value.cohort} value={value.cohort}>
                    {shortDigest(value.cohort)} · {value.implementationCount} targets
                  </option>
                ))}
              </select>
            </label>
          </> : <label>
            <span>Contract cohort</span>
            <select value={familyCohort} onChange={(event) => setFamilyCohort(event.target.value)}>
              {(family === 'lifecycle' ? dataset?.lifecycleGroups : dataset?.buildGroups)?.map((item) => (
                <option key={item.cohort} value={item.cohort}>
                  {shortDigest(item.cohort)} · {item.implementationCount} targets
                </option>
              ))}
            </select>
          </label>}
          <div className="data-revision">
            <span>Data revision</span>
            <code title={DATA_REVISION}>{shortDigest(DATA_REVISION)}</code>
          </div>
        </section>

        {datasetError ? <ErrorState message={datasetError} /> : null}
        {!dataset && !datasetError ? <LoadingState /> : null}
        {dataset && family === 'service' && !group && !datasetError ? <EmptyState /> : null}
        {dataset && family !== 'service' && !activeFamilyGroup && !datasetError ? <EmptyState /> : null}
        {family === 'service' && group ? (
          <>
            {evidenceError ? <div className="evidence-error" role="alert">Evidence request failed: {evidenceError}</div> : null}
            <ComparisonSection
              group={group}
              results={results}
              selectedTarget={selectedTarget}
              onSelect={setSelectedTarget}
            />
            {selectedItem ? (
              <DetailSection
                detail={detail}
                group={group}
                item={selectedItem}
                loading={detailLoading}
                selectedTrial={selectedTrial}
                onTrialChange={setSelectedTrial}
              />
            ) : null}
          </>
        ) : null}
        {family === 'lifecycle' && lifecycleGroup ? <LifecycleSection group={lifecycleGroup} /> : null}
        {family === 'build' && buildGroup ? <BuildSection group={buildGroup} /> : null}
      </main>
    </div>
  )
}

function ComparisonSection({
  group,
  onSelect,
  results,
  selectedTarget,
}: {
  group: ComparisonGroup
  onSelect: (implementation: string) => void
  results: Map<string, unknown[]>
  selectedTarget: string
}) {
  return (
    <section className="section comparison-section">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Comparable cohort</span>
          <h2>{label(group.scenario)}</h2>
          <p>Median of three valid trials under the exact same contract and traffic profile.</p>
        </div>
        <div className="cohort-id"><span>Cohort</span><code title={group.cohort}>{shortDigest(group.cohort)}</code></div>
      </div>
      <div className="comparison-table-wrap">
        <table className="comparison-table">
          <thead>
            <tr>
              <th>Implementation</th>
              <th>Throughput</th>
              <th>p50</th>
              <th>p95</th>
              <th>p99</th>
              <th>Errors</th>
              <th>CPU avg / max</th>
              <th>Memory max</th>
              <th><span className="sr-only">Inspect</span></th>
            </tr>
          </thead>
          <tbody>
            {group.items.map((item) => {
              const implementation = item.entry.selection.implementation
              const target = comparisonItemKey(item.entry)
              const resources = summarizeTrialResources(results.get(item.entry.run_set_id) ?? [])
              const metrics = item.runSet.summary.runtime_metrics
              const active = target === selectedTarget
              return (
                <tr className={active ? 'selected-row' : ''} key={item.entry.run_set_id}>
                  <td>
                    <strong>{implementationLabel(implementation)}</strong>
                    <span>{item.entry.selection.variant}</span>
                  </td>
                  <MetricCell metric={metrics.rps} unit="req/s" digits={1} />
                  <MetricCell metric={metrics.p50_ms} unit="ms" digits={2} />
                  <MetricCell metric={metrics.p95_ms} unit="ms" digits={2} />
                  <MetricCell metric={metrics.p99_ms} unit="ms" digits={2} />
                  <MetricCell metric={metrics.error_rate} unit="%" digits={3} multiplier={100} />
                  <td className="resource-cell">{formatNumber(resources.cpuAveragePercent, 1)} / {formatNumber(resources.cpuMaxPercent, 1)}<small>%</small></td>
                  <td className="resource-cell">{formatBytes(resources.memoryMaxBytes)}</td>
                  <td>
                    <button
                      aria-label={`Inspect ${targetLabel(item.entry)}`}
                      className="inspect-button"
                      onClick={() => onSelect(target)}
                      title={`Inspect ${targetLabel(item.entry)}`}
                      type="button"
                    >
                      <ArrowUpRight size={16} />
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function FamilyButton({
  active,
  children,
  onClick,
}: {
  active: boolean
  children: React.ReactNode
  onClick: () => void
}) {
  return <button aria-pressed={active} className={active ? 'active' : ''} onClick={onClick} type="button">{children}</button>
}

function LifecycleSection({ group }: { group: LifecycleGroup }) {
  return (
    <SummarySection
      description="Startup readiness across every valid lifecycle trial."
      group={group}
      metricLabel={label}
      title="Cold start"
    />
  )
}

function BuildSection({ group }: { group: BuildGroup }) {
  const metricLabels: Record<string, string> = {
    gradle_clean_build_ms: 'Gradle clean build',
    gradle_incremental_rebuild_ms: 'Gradle incremental rebuild',
    image_package_ms: 'Image package',
    image_rebuild_ms: 'Image rebuild',
  }
  return (
    <SummarySection
      description="Build duration summaries across three valid fresh-workspace trials."
      group={group}
      metricLabel={(name) => metricLabels[name] ?? label(name)}
      title="Build"
    />
  )
}

function SummarySection({
  description,
  group,
  metricLabel,
  title,
}: {
  description: string
  group: LifecycleGroup | BuildGroup
  metricLabel: (name: string) => string
  title: string
}) {
  const rows = group.items.flatMap((item) => {
    const metrics = 'build_metrics' in item.runSet.summary
      ? item.runSet.summary.build_metrics
      : item.runSet.summary.startup_metrics
    return Object.entries(metrics).map(([name, metric]) => ({ item, metric, name }))
  })
  return (
    <section className="section summary-section">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Comparable cohort</span>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <div className="cohort-id"><span>Cohort</span><code title={group.cohort}>{shortDigest(group.cohort)}</code></div>
      </div>
      <div className="summary-table-wrap">
        <table className="summary-table">
          <thead>
            <tr>
              <th>Implementation</th>
              <th>Metric</th>
              <th>Median</th>
              <th>Min / max</th>
              <th>Trials</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ item, metric, name }) => (
              <tr key={`${item.entry.run_set_id}\0${name}`}>
                <td><strong>{implementationLabel(item.entry.selection.implementation)}</strong><span>{item.entry.selection.variant}</span></td>
                <td>{metricLabel(name)}</td>
                <td className="summary-number"><strong>{formatDuration(metric.median)}</strong></td>
                <td className="summary-number">{formatDuration(metric.min)} / {formatDuration(metric.max)}</td>
                <td className="summary-trials">{metric.trials.map((trial) => `${trial.trial_id}: ${formatDuration(trial.value)}`).join(' · ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function MetricCell({
  digits,
  metric,
  multiplier = 1,
  unit,
}: {
  digits: number
  metric?: { min: number; median: number; max: number }
  multiplier?: number
  unit: string
}) {
  if (!metric) return <td className="metric-cell muted">n/a</td>
  return (
    <td className="metric-cell">
      <strong>{(metric.median * multiplier).toFixed(digits)}</strong><small>{unit}</small>
      <span>{(metric.min * multiplier).toFixed(digits)}–{(metric.max * multiplier).toFixed(digits)}</span>
    </td>
  )
}

function DetailSection({
  detail,
  group,
  item,
  loading,
  onTrialChange,
  selectedTrial,
}: {
  detail: TrialDetail | null
  group: ComparisonGroup
  item: ComparisonItem
  loading: boolean
  onTrialChange: (trial: number) => void
  selectedTrial: number
}) {
  const samples = detail?.samples ?? []
  const hasRuntime = samples.some((sample) => typeof sample.achieved_rps === 'number')
  const result = asRecord(detail?.result)
  const environment = asRecord(result.environment)
  const publication = asRecord(detail?.publication)
  const manifest = asRecord(detail?.manifest)
  const cohort = asRecord(manifest.cohort)
  const contracts = asRecord(cohort.contracts)
  const selectedTrialPath = item.runSet.trials.find((trial) => trial.index === selectedTrial)?.path
  return (
    <section className="section detail-section">
      <div className="section-heading detail-heading">
        <div>
          <span className="eyebrow">Run detail</span>
          <h2>{targetLabel(item.entry)}</h2>
          <p>{label(group.loadProfile)} · measured window · {formatInterval(detail?.sampleIntervalMs)} intervals · warmup excluded</p>
        </div>
        <div className="trial-tabs" aria-label="Trial">
          {item.runSet.trials.map((trial) => (
            <button
              aria-pressed={selectedTrial === trial.index}
              className={selectedTrial === trial.index ? 'active' : ''}
              key={trial.trial_id}
              onClick={() => onTrialChange(trial.index)}
              type="button"
            >
              Trial {trial.index}
            </button>
          ))}
        </div>
      </div>
      {loading ? <div className="chart-loading">Loading trial evidence…</div> : null}
      {!loading && !hasRuntime ? (
        <div className="timeline-notice">Request and latency timelines are not available in this older dataset revision. Resource samples remain visible.</div>
      ) : null}
      {!loading && samples.length > 0 ? (
        <div className="chart-grid">
          <ChartPanel title="Traffic" detail="Requested and achieved arrival rate">
            <TimelineChart label="Traffic timeline" option={trafficOption(samples)} />
          </ChartPanel>
          <ChartPanel title="Latency" detail="Response-time percentiles">
            <TimelineChart label="Latency timeline" option={latencyOption(samples)} />
          </ChartPanel>
          <ChartPanel title="Errors" detail="Failed request ratio">
            <TimelineChart label="Error timeline" option={errorOption(samples)} />
          </ChartPanel>
          <ChartPanel title="CPU" detail="Usage by benchmark role">
            <TimelineChart label="CPU timeline" option={resourceOption(samples)} />
          </ChartPanel>
          <ChartPanel title="Memory" detail="Working set by benchmark role" wide>
            <TimelineChart label="Memory timeline" option={memoryOption(samples)} />
          </ChartPanel>
        </div>
      ) : null}
      <div className="provenance-grid">
        <ProvenanceItem icon={<GitBranch size={16} />} label="Source commit" value={shortDigest(item.entry.source_commit)} title={item.entry.source_commit} />
        <ProvenanceItem icon={<Server size={16} />} label="Image digest" value={shortDigest(item.entry.image_digest.replace('sha256:', ''))} title={item.entry.image_digest} />
        <ProvenanceItem icon={<Database size={16} />} label="Environment" value={stringValue(environment.os_image) || 'home-k3s-v1'} title={stringValue(environment.kubernetes_version)} />
        <ProvenanceItem icon={<Activity size={16} />} label="Run set" value={shortRunId(item.entry.run_set_id)} title={item.entry.run_set_id} />
      </div>
      <div className="evidence-footer">
        <div className="contract-list">
          <ContractVersion name="Scenario" contract={contracts.scenario} />
          <ContractVersion name="Environment" contract={contracts.environment_profile} />
          <ContractVersion name="Protocol" contract={contracts.measurement_protocol} />
        </div>
        <nav aria-label="Evidence links" className="evidence-links">
          <EvidenceLink href={stringValue(publication.workflow_url)}>Workflow</EvidenceLink>
          <EvidenceLink href={stringValue(publication.raw_artifact_url)} title={stringValue(publication.raw_artifact_sha256)}>Raw evidence</EvidenceLink>
          <EvidenceLink href={datasetUrl(`${item.entry.path}/publication.json`)} title={item.entry.publication_sha256}>Publication</EvidenceLink>
          <EvidenceLink href={datasetUrl(`${item.entry.path}/resolved-manifest.json`)}>Manifest</EvidenceLink>
          <EvidenceLink href={selectedTrialPath ? datasetUrl(`${item.entry.path}/${trialDirectory(selectedTrialPath)}/artifact-manifest.json`) : ''}>Trial artifacts</EvidenceLink>
          <EvidenceLink href={`https://github.com/${REPOSITORY}/blob/${item.entry.source_commit}/docs/methodology.md`}>Methodology</EvidenceLink>
        </nav>
      </div>
    </section>
  )
}

function ChartPanel({ children, detail, title, wide = false }: { children: React.ReactNode; detail: string; title: string; wide?: boolean }) {
  return <article className={`chart-panel${wide ? ' chart-panel-wide' : ''}`}><header><h3>{title}</h3><p>{detail}</p></header><Suspense fallback={<div className="chart-placeholder" />}>{children}</Suspense></article>
}

function ProvenanceItem({ icon, label: itemLabel, title, value }: { icon: React.ReactNode; label: string; title?: string; value: string }) {
  return <div className="provenance-item" title={title}><span className="provenance-icon">{icon}</span><div><span>{itemLabel}</span><strong>{value}</strong></div></div>
}

function ContractVersion({ contract, name }: { contract: unknown; name: string }) {
  const value = asRecord(contract)
  const version = stringValue(value.contract_version)
  return version ? <span>{name} v{version}</span> : null
}

function EvidenceLink({ children, href, title }: { children: React.ReactNode; href: string; title?: string }) {
  if (!href) return null
  return <a href={href} rel="noreferrer" target="_blank" title={title}>{children}<ExternalLink size={13} /></a>
}

function LoadingState() {
  return <div className="state-view"><div className="loading-line" /><strong>Loading benchmark evidence</strong><p>Reading the pinned GitHub dataset.</p></div>
}

function ErrorState({ message }: { message: string }) {
  return <div className="state-view error-state"><strong>Dataset unavailable</strong><p>{message}</p></div>
}

function EmptyState() {
  return <div className="state-view"><strong>No complete run sets</strong><p>The selected dataset has no validated comparison cohort.</p></div>
}

function trialDirectory(path: string): string {
  return path.replace(/\/trial\.json$/, '')
}

function unique(values: string[]): string[] {
  return [...new Set(values)].sort((left, right) => label(left).localeCompare(label(right)))
}

function label(value: string): string {
  return value.split(/[-_]/).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ')
}

function implementationLabel(value: string): string {
  return value === 'java/spring-boot' ? 'Spring Boot' : value === 'java/quarkus' ? 'Quarkus' : value
}

function targetLabel(entry: CatalogEntry): string {
  return `${implementationLabel(entry.selection.implementation)} · ${entry.selection.variant}`
}

function shortDigest(value: string): string {
  return value.length > 12 ? value.slice(0, 12) : value
}

function shortRunId(value: string): string {
  const match = value.match(/^([^_]+_[^_]+_[^_]+)/)
  return match?.[1] ?? value.slice(0, 24)
}

function formatNumber(value: number | undefined, digits: number): string {
  return value === undefined ? 'n/a' : value.toFixed(digits)
}

function formatBytes(value: number | undefined): string {
  return value === undefined ? 'n/a' : `${(value / 1024 / 1024).toFixed(0)} MiB`
}

function formatInterval(value: number | undefined): string {
  if (!value) return 'unknown'
  return value % 1000 === 0 ? `${value / 1000}-second` : `${value}-millisecond`
}

function formatDuration(value: number): string {
  return value >= 1_000 ? `${(value / 1_000).toFixed(2)} s` : `${value.toFixed(0)} ms`
}

function familyFromSearch(): EvidenceFamily {
  const value = new URLSearchParams(window.location.search).get('family')
  return value === 'lifecycle' || value === 'build' ? value : 'service'
}

function datasetUrl(path: string): string {
  return `https://raw.githubusercontent.com/${REPOSITORY}/${DATA_REVISION}/${path}`
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export default App
