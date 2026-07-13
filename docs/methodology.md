# Methodology

Hello Real World Bench records benchmark evidence across several categories.

## Measurement Categories

### Build Metrics

Build metrics capture how long it takes to produce runnable application artifacts and Docker images.

The MVP records Gradle clean build time and Docker image build time separately. The Docker image build should package the artifact produced by the Gradle build step instead of rebuilding the application inside Docker. Gradle dependency cache and Docker layer cache state are recorded in `build.cache`; early runs should not be compared unless cache conditions and run order are understood.

### Startup Metrics

Startup metrics capture how long the target container takes until the scenario endpoint first returns a successful response.

For `cold-start-api`, startup means the time from starting the target container until `/ping` first returns HTTP 200. The successful `/ping` request latency is also recorded. The MVP measures this repeatedly on the same host by stopping and starting the target container between samples.

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
target, dependency, load-generator, and host resource sample. Per-request
events are not retained; raw evidence remains bounded while preserving the
timeline needed to inspect load transitions and recovery behavior.

For HTTP aggregation scenarios, target outbound HTTP client settings and mock upstream settings are part of the benchmark contract. The baseline is documented in [http-baseline.md](http-baseline.md).

For `read-heavy-query-api`, PostgreSQL initializes an immutable 100,000-row
catalog before the target starts. The runner verifies the complete dataset
fingerprint and required index before target startup and again after every
measured trial. k6 independently checks filtering, ordering, cursor, field, and
response-size semantics on every response.

Some scenarios, such as `cold-start-api`, do not run a sustained k6 load phase. In those cases the k6 summary files record that load was skipped, and the startup result is the primary measurement.

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

Local and calibration outputs are development evidence. Official results require
clean trusted commits, three valid trials, frozen profiles, complete correctness
evidence, and the home-k3s validity checks.
