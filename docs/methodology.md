# Methodology

Hello Real World Bench records benchmark evidence across several categories.

## Measurement Categories

### Build Metrics

Build metrics capture how long it takes to produce runnable application artifacts and Docker images.

### Startup Metrics

Startup metrics capture how long a container takes to become ready and how long the first request to the scenario endpoint takes.

### Runtime Metrics

Runtime metrics come from the scenario load test. Average latency is less important than p95 and p99 latency because tail behavior is usually more relevant to service reliability.

### Resource Metrics

Resource metrics capture basic CPU and memory usage from Docker. The MVP collects a one-shot snapshot after the benchmark run.

### Environment Metadata

Results should include host and environment metadata such as OS, CPU, memory, Docker availability, load generator placement, scenario, and implementation.

## Load Generator Placement

For the MVP, the load generator runs on the same host as the target container. This is acceptable for validating runner automation, but it must be documented in every result.

A later phase should add remote load generator mode.

## Interpretation

Benchmark results should be phrased as trade-offs under specific scenario conditions. Avoid universal claims such as one framework being faster than another in general.
