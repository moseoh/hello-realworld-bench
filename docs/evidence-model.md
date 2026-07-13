# Evidence Model

Benchmark evidence is split into four Draft 2020-12 JSON documents. Each document
has one responsibility and rejects unknown properties.

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

## Versioning

All four contracts start at `schema_version` `1.0`. A change that renames a field,
moves a field, changes its meaning, or changes digest canonicalization requires a
schema version change. Adding a metric does not change the schema because metric
identity and units are data.
