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

Some scenarios, such as `cold-start-api`, do not run a sustained k6 load phase. In those cases the k6 summary files record that load was skipped, and the startup result is the primary measurement.

### Resource Metrics

Resource metrics capture basic CPU and memory usage from Docker. For load-test scenarios, the MVP samples Docker stats while the benchmark k6 phase is running and records average and maximum CPU and memory summaries. For load-disabled scenarios, the runner records a one-shot Docker stats snapshot after startup measurement.

### Environment Metadata

Results should include host and environment metadata such as OS, CPU, memory, Docker availability, load generator placement, scenario, and implementation.

Variant metadata should be recorded separately from implementation metadata. For example, `java/spring-boot` identifies the implementation, while `jvm-java25` identifies the build and runtime variant.

The normalized result shape is documented in [results-schema.md](results-schema.md). Raw scenario artifacts may contain more detail than `result.json`.

## Load Generator Placement

For the MVP, the load generator runs on the same host as the target container. This is acceptable for validating runner automation, but it must be documented in every result.

A later phase should add remote load generator mode.

## Interpretation

Benchmark results should be phrased as trade-offs under specific scenario conditions. Avoid universal claims such as one framework being faster than another in general.
