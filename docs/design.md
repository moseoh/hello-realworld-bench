# Design

Hello Real World Bench is a small benchmark automation platform.

The execution architecture is:

```text
runner
  ↓
Docker Compose (development) or k3s (official)
  ↓
target implementation
  ↕
scenario dependencies
  ↓
scenario load test
  ↓
validated result evidence
```

## Runner

The runner is a uv-managed Python entry point wrapped by the top-level Makefile:

```bash
make run
```

It coordinates cleanup, build measurement, image build measurement, startup measurement, warmup, load test execution, Docker stats collection, and result persistence.

Runner behavior is configuration-driven:

- implementation ownership, default variant, and default build profile: `implementations/<language>/<framework>/implementation.yaml`
- runtime and image metadata: `implementations/<language>/<framework>/variants/<variant>.yaml`
- service behavior and default load, environment, and measurement profiles: `scenarios/<scenario>/scenario.yaml`
- load, environment, measurement, and build profile catalogs: `contracts/`

The complete ownership model and current catalog status are documented in
[benchmark-contracts.md](benchmark-contracts.md). Local Docker Compose and home
k3s calibration runs produce development evidence. Frozen home k3s contracts
produce official evidence only after all trial and publication validity gates pass.
Run resolution rejects any selected draft profile.

Before measurement, the runner persists and validates a checkout-bound resolved
manifest. That manifest is the source of the ordered Compose overlay list and links
the exact inputs to result metadata. See [resolved-run-manifest.md](resolved-run-manifest.md).

## Build Measurement

The MVP records two separate build phases:

- Gradle clean build: produces application artifacts using an implementation-local Gradle cache.
- Docker image build: packages the already-built application artifact into the target runtime image with Docker layer cache enabled.

These are intentionally separate because code-change feedback and image packaging can behave differently. Later build profiles should make cache state explicit, such as cold dependency cache, warm dependency cache, Docker cache enabled, and Docker cache disabled.

## Execution Profiles

Docker Compose is the local development profile. The official profile uses the
fixed single-node home k3s environment with separately limited target,
dependency, and in-cluster k6 workloads.

The target service is always named `target` so scenarios can interact with a stable contract.

The official environment contract no longer owns a framework-specific image
repository. Each implementation contract owns its repository, while
`home-k3s-v1` defines the shared host and resource conditions. This ownership
change produced environment contract version `1.2` and starts a new comparison
cohort.

## Target Implementation

Implementation source is organized by language and framework:

```text
implementations/
  java/
    spring-boot/
    quarkus/
```

The baseline implementations are Spring Boot 4 with Java 25 and Quarkus
`3.33.2.1` LTS with Java 25. Spring Boot comes from Spring Initializr; Quarkus
comes from the official Quarkus generator. Both implement:

- `GET /ping`
- transactional command handling with PostgreSQL
- parallel upstream HTTP aggregation

They preserve the same request and response contracts, PostgreSQL transaction
and outbox behavior, and effective outbound HTTP client limits and timeouts.

Redis, message brokers, tracing stacks, and service meshes are not part of the
current core benchmark.

## Variants

A variant is a build or runtime choice inside one implementation. For example, Java version, Spring Boot version, JVM mode, native image mode, or virtual threads can be represented as variants when the service code remains the same.

The first variant is:

```text
implementations/java/spring-boot/variants/jvm-java25.yaml
```

Create a separate implementation folder only when the implementation source or framework family changes, such as Spring MVC versus WebFlux, Spring Boot versus Quarkus, or Java versus Go.

## Scenario Load Tests

`ping-api` validates the runner path and is not a performance conclusion. Core
service qualification uses `transactional-command-api` and
`io-aggregation-api` under frozen steady, capacity-ramp, and burst-recovery load
profiles.

Transactional scenario contract version `1.2` raises immediately available k6
VUs from 100 to 200. Calibration at the 1,000 requests/second burst observed a
113 ms tail and four dropped iterations while dynamic VU allocation caught up;
the higher reservation removes that load-generator artifact from future cohorts.

## Result Output

Each run writes a timestamped directory under `results/`. Outputs are intended to be machine-readable where practical, with raw logs retained for debugging.

Results mirror the implementation layout:

```text
results/java/spring-boot/jvm-java25/ping-api/<run_id>/
results/java/quarkus/jvm-java25/ping-api/<run_id>/
```

The normalized `result.json` contract is documented in [results-schema.md](results-schema.md). `resolved-manifest.json` records exact inputs and comparison-cohort identity. Scenario-specific raw details remain in files such as `startup.json` and `k6-summary.json`.

The first target is runner correctness, not performance ranking.
