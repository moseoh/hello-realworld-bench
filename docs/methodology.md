# Methodology

Hello Real World Bench records benchmark evidence across several categories.

## Measurement Categories

### Build Metrics

Build metrics capture how long it takes to produce runnable application
artifacts and Docker images. An official build run set contains exactly three
trials. Every trial starts from a fresh copy of the source tree, a fresh copy of
the immutable seeded Gradle dependency cache, and a fresh Buildx builder.

The frozen operation order is `gradle_clean_build`, `image_package`,
`gradle_incremental_rebuild`, and `image_rebuild`. Their normalized metrics are
`gradle_clean_build_ms`, `image_package_ms`,
`gradle_incremental_rebuild_ms`, and `image_rebuild_ms`. The incremental step
changes the declared source probe from `0` to `1` and must change the application
artifact. Both image steps package the application artifact produced by Gradle;
they do not rebuild the application inside Docker.

The dependency cache is an immutable fresh-copy seed. `image_package` receives
only the pinned runtime-base cache, while `image_rebuild` receives the cache
exported by the first package operation. Commands, source-tree digests, probe
digests, application artifacts, OCI artifacts, logs, and operation boundaries
are hash-bound in the raw evidence. Comparisons are valid only when the frozen
environment, measurement, and build profile contracts match. Official build
evidence also pins rootless Docker Engine 29.6.1, Docker Buildx 0.35.0,
effective UID 1000, and `unix:///run/user/1000/docker.sock`. This daemon is
separate from k3s containerd, and its `systemd` cgroup driver enforces the
BuildKit CPU and memory limits declared by the build profile. The frozen Java
executor user is container `0:0`, which rootless Docker maps to host UID 1000
for writable bind mounts without granting host-root access.

### Startup Metrics

Startup metrics capture how long a new target process takes to complete the scenario's first valid business response.

For official `cold-start-api` evidence, the start boundary is a millisecond timestamp emitted by the image entrypoint immediately before it calls `exec` for the application process. The same marker logic is used by every implementation. A native sidecar is armed before the target starts and observes the first exact `/ping` contract over Pod-localhost. The completion boundary is the response completion timestamp. The immutable target and observer images are pre-pulled, and measured Pods use `imagePullPolicy: Never`; image transfer, pulls, scheduling, and Service propagation are therefore excluded. The successful request latency is recorded separately.

Each official lifecycle set contains five fresh target containers with a fixed five-second quiet interval between trials. It represents a new JVM process on a warm node with warm image and filesystem caches. The marker is immediately before, but is not the kernel's exact `execve(2)` timestamp. Marker output and shell-to-JVM `exec` overhead are included in the interval. It is not a machine cold boot or a serverless platform cold start. Every boundary timestamp, observer attempt count, Pod state, image identity, and restart count remains in the evidence bundle.

Lifecycle validity uses run-level preflight and postflight checks plus one node-background snapshot immediately before and after every measured startup. It does not reuse the service protocol's ten-second in-run coverage rule: kubelet sampling cannot resolve every transient inside a sub-second lifecycle interval. This limitation remains part of the evidence contract instead of being represented as false high-frequency coverage.

For scenarios with support services, such as PostgreSQL or mock upstream HTTP services, the runner starts those dependencies first and waits for them before measuring the target container. Dependency startup time is recorded separately as `dependency_ready_ms`; target startup time is recorded as `ready_ms`.

The runner intentionally does not use framework health endpoints for cold-start measurement. Health endpoints can warm different parts of different frameworks and distort the first business endpoint response.

### Runtime Metrics

Runtime metrics come from the scenario load test. Average latency is less important than p95 and p99 latency because tail behavior is usually more relevant to service reliability.

The MVP extracts request rate, p50, p95, p99, and error rate from the k6 summary JSON.
Official service profiles use deterministic open arrival rates. `steady` holds
the scenario base rate, `capacity-ramp` increases from 0.25x through 2x, and
`burst-recovery` applies immediate 3x and 5x spikes separated by base-rate
recovery windows. Dropped scheduled iterations invalidate a trial as load
generator capacity failure.

Core service scripts also emit bounded 10-second k6 aggregates. Each bucket
records requested and achieved request rate, semantic failure rate, and p50,
p95, and p99 HTTP latency. The k3s runner aligns those buckets with the nearest
target, dependency, load-generator, and host resource sample. Requests are
assigned by request start time, including responses that finish during k6's
graceful-stop window. k6's scenario-start timestamp and kubelet's UTC sample
timestamps provide the shared time origin, so Pod scheduling time is excluded.
Per-request
events are not retained; raw evidence remains bounded while preserving the
timeline needed to inspect load transitions and recovery behavior.

For HTTP aggregation scenarios, target outbound HTTP client settings and mock upstream settings are part of the benchmark contract. The baseline is documented in [http-baseline.md](http-baseline.md).

For `read-heavy-query-api`, PostgreSQL initializes an immutable 100,000-row
catalog before the target starts. The runner verifies the complete dataset
fingerprint and required index before target startup and again after every
measured trial. k6 independently checks filtering, ordering, cursor, field, and
response-size semantics on every response.

Some scenarios, such as `cold-start-api`, do not run a sustained load phase. k6 acts only as the pre-armed Pod-localhost lifecycle observer, and lifecycle timing is the primary measurement.

### Resource Metrics

Resource metrics capture basic CPU and memory usage from Docker. For load-test scenarios, the MVP samples Docker stats while the benchmark k6 phase is running and records average and maximum CPU and memory summaries. For load-disabled scenarios, the runner records a one-shot Docker stats snapshot after startup measurement.

### Environment Metadata

Results should include host and environment metadata such as OS, CPU, memory, Docker availability, load generator placement, scenario, and implementation.

Variant metadata should be recorded separately from implementation metadata. For example, `java/spring-boot` identifies the implementation, while `jvm-java25` identifies the build and runtime variant.

The normalized result shape is documented in [results-schema.md](results-schema.md). Raw scenario artifacts may contain more detail than `result.json`.

Every run records an exact-run manifest before measurement. Its complete digest
identifies resolved contracts, effective settings, assets, and Git worktree
provenance. Its narrower cohort fingerprint identifies shared scenario and
measurement conditions while excluding implementation-specific inputs. See
[resolved-run-manifest.md](resolved-run-manifest.md).

## Load Generator Placement

For local development, the load generator runs on the same host as the target
container. The official profile runs k6 as a separately limited in-cluster Job
on the same physical k3s node. Both placements are recorded in the resolved
environment contract.

A later phase should add remote load generator mode.

## Interpretation

Benchmark results should be phrased as trade-offs under specific scenario conditions. Avoid universal claims such as one framework being faster than another in general.

Local and calibration outputs are development evidence. Official service and
build results require three valid trials; official lifecycle results require
five valid trials. All require clean trusted commits, frozen profiles, complete
correctness evidence, immutable artifacts, and the applicable host validity
checks.
