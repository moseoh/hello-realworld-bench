# Hello Real World Bench

Backend runtime benchmarks beyond Hello World.

Hello Real World Bench compares backend runtimes and frameworks through practical
service patterns, repeatable measurements, and auditable evidence. Results
describe trade-offs under a specific scenario and environment; they are not
universal performance rankings.

**[View the public benchmark dashboard](https://moseoh.github.io/hello-realworld-bench/)**

## Current Scope

Supported implementations run on Java 25:

| Implementation | Variants |
| --- | --- |
| Spring Boot 4 | `jvm-java25`, `jvm-java25-virtual-threads` |
| Quarkus 3.33.2.1 LTS | `jvm-java25` |

The published core comparison baseline currently uses `jvm-java25` for both
implementations.

Core service scenarios:

- `transactional-command-api`: validation, domain logic, a database transaction,
  and an outbox insert
- `read-heavy-query-api`: indexed reads, pagination, and bounded JSON responses
- `io-aggregation-api`: parallel upstream HTTP calls and response aggregation

Supporting scenarios:

- `ping-api`: runner qualification
- `cold-start-api`: process start to first valid business response
- `io-aggregation-timeout-api`: degraded upstream behavior

Official service results use three traffic profiles: `steady`, `capacity-ramp`,
and `burst-recovery`. Build and cold-start measurements are separate evidence
families.

## Quick Start

Requirements:

- Docker with Docker Compose
- [uv](https://docs.astral.sh/uv/)
- Java 25
- k6, or Docker for the runner fallback

Run the default local benchmark:

```bash
make run
```

Select an implementation and scenario:

```bash
make run \
  IMPLEMENTATION=java/quarkus \
  SCENARIO=transactional-command-api \
  VARIANT=jvm-java25
```

Run the contract-defined repeatable trial set:

```bash
make run-set \
  IMPLEMENTATION=java/spring-boot \
  SCENARIO=io-aggregation-api \
  VARIANT=jvm-java25
```

Results are written under:

```text
results/<language>/<framework>/<variant>/<scenario>/<run-id>/
```

## How It Works

```text
versioned contracts
        |
        v
Python runner -> Docker Compose or k3s -> target and dependencies
        |                                  |
        +------------ k6 load -------------+
        |
        v
validated JSON evidence -> benchmark-data -> static dashboard
```

Local development uses Docker Compose. Official service and cold-start
measurements run serially on a frozen single-node home k3s environment. Official
build measurements use a separate frozen rootless-Docker profile. A private
trusted controller accepts only public `main` commits and publishes validated
results to the append-only `benchmark-data` branch.

## Development

Run all contract, runner, implementation, and dashboard checks:

```bash
npm --prefix dashboard ci
make check
```

Development checks additionally require Node.js 24.

Run the dashboard locally:

```bash
make dashboard-dev
```

## Documentation

- [Benchmark contracts](docs/benchmark-contracts.md)
- [Methodology](docs/methodology.md)
- [Scenarios](docs/scenarios.md)
- [Fairness rules](docs/fairness.md)
- [Home k3s environment](docs/k3s.md)
- [Automation and publication](docs/automation.md)
- [Evidence model](docs/evidence-model.md)
- [Roadmap](docs/roadmap.md)

## Result Interpretation

Compare results only when the implementation, variant, scenario, load profile,
environment profile, and measurement protocol form a compatible cohort. The
project reports measured trade-offs and does not claim that one runtime is
universally faster than another.
