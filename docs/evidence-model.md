# Evidence Model

Service and lifecycle benchmark evidence is split into four Draft 2020-12 JSON
documents. Build evidence uses a parallel set of strict JSON documents. Each
document has one responsibility and rejects unknown properties.

## Documents

`trial.json` describes one timed execution. It binds the execution to the exact
resolved manifest and comparison cohort, records its validity outcome, and carries
the normalized summary metrics. Every summary metric names the raw artifact paths
from which it was calculated.

`time-series.json` contains compact normalized runtime samples for the trial.
For core service scenarios, each 10-second row combines requested and achieved
load, request and failure counts, p50/p95/p99 latency, and the nearest target,
dependency, load-generator, and host resource sample. `elapsed_ms` is measured
from the start of benchmark collection. A `null` value means that a metric was
unavailable for that sample; missing samples must not be converted to zero.
Per-request events are not part of compact evidence.
Core scenario producers reject missing or partial timeline metrics. Rates and
latencies must be non-negative, and error rates must remain between zero and one.

`artifact-manifest.json` inventories the immutable raw evidence for the trial.
Paths are relative to the trial directory. Each entry records the file size and
SHA-256 digest, with an optional media type. The manifest does not embed raw data
or prescribe collector-specific filenames. To avoid circular hashes, it excludes
`trial.json`, `time-series.json`, and itself.

`run-set.json` groups independent trials executed by one resolved run manifest.
Its `run_id`, `manifest_digest`, and `cohort_fingerprint` bind every referenced
trial to identical resolved inputs and comparison conditions. `trial_id`
distinguishes repetitions within that run.
The run-set summary retains every contributing trial value alongside min, median,
and max; it does not select a favorable repetition.

`build-resolved-manifest.json` binds a build run to the exact implementation and
variant inputs while deriving its comparison cohort only from the shared
environment, measurement, and build profiles. `build-trial.json` records the
four exact operation commands, timings, source and probe transitions, cache and
builder identities, and artifact references. `build-run-set.json` requires
exactly three valid trials and retains every contributing value before computing
the summary. Its trial artifact manifests inventory every application and OCI
artifact used by the semantic validator.

## Traceability

The evidence chain is:

```text
run-set.json
  -> trial.json
       -> resolved-manifest.json
       -> time-series.json
       -> artifact-manifest.json
            -> raw artifacts
```

Each arrow to an evidence document is protected by a SHA-256 digest. `run_id` and
`manifest_digest` must agree across the run set, trial, and resolved manifest. A
trial's `cohort_fingerprint` must agree with both. `trial_id` must agree across the
trial, time series, and artifact manifest. Artifact paths cited by summary metrics
must exist in that trial's raw artifact manifest.

JSON Schema validates each document in isolation. The evidence producer or
publication validator must additionally enforce the cross-document rules above,
unique artifact paths and metric names, monotonically increasing `elapsed_ms`,
`finished_at` not preceding `started_at`, and run-set equality for manifest and
cohort digests. Invalid or failed
trials remain auditable evidence but must not contribute to promoted run-set
summaries.

The build evidence chain is:

```text
build-run-set.json
  -> build-resolved-manifest.json
  -> preflight.json
  -> postflight.json
  -> cache-seed.json
  -> build-trial.json
       -> artifact-manifest.json
            -> operation records, logs, and build artifacts
```

The hosted publication validator recalculates every reference and artifact hash,
then compares commands, trees, probes, artifacts, cache semantics, and summaries
with the frozen contracts. It also enforces a closed regular-file set, so an
undeclared regular file or any symlink invalidates the raw build evidence.

## Versioning

All current contracts start at `schema_version` `1.0`. A change that renames a
field, moves a field, changes its meaning, or changes digest canonicalization
requires a schema version change. Adding a metric does not change the schema
because metric identity and units are data.
