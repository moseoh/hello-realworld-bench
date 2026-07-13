# Hello Real World Bench

Backend runtime benchmarks beyond Hello World.

Hello Real World Bench compares backend runtimes and frameworks using practical service patterns such as transactional APIs, I/O aggregation, cold starts, and build/startup metrics.

The project stays small: two Java implementations, a focused scenario catalog,
and one repeatable benchmark runner.

## What This Is

Hello Real World Bench is an experimental benchmark automation platform for backend runtimes and frameworks. It focuses on service-pattern scenarios and repeatable measurement workflows.

The first milestone validates the runner itself:

- build measurement
- Docker image build measurement
- startup first-success measurement
- k6 load execution
- Docker resource snapshot collection
- timestamped machine-readable result output

## What This Is Not

This is not a final performance ranking. It is not a replacement for large benchmark suites such as TechEmpower. It is not intended to claim that one runtime is universally faster than another.

Early results should be read only as trade-offs under the exact scenario, host, resource limits, and load profile used for that run.

## Current Status

Experimental MVP.

Supported implementations:

- `java/spring-boot` with the `jvm-java25` and `jvm-java25-virtual-threads` variants
- `java/quarkus` with the `jvm-java25` variant, using Quarkus `3.33.2.1` LTS
  and Java 25

Supported scenarios:

- `ping-api`
- `cold-start-api`
- `transactional-command-api`
- `read-heavy-query-api`
- `io-aggregation-api`
- `io-aggregation-timeout-api`

The `ping-api` scenario exists to validate benchmark runner automation. It is not a meaningful real-world performance conclusion by itself.

The `cold-start-api` scenario measures repeated time to first successful `/ping` response after the application starts. It does not model serverless platform cold starts.

The profile catalog supports local development, short home-k3s calibration, and
frozen official service runs. Official open-model profiles are `steady`,
`capacity-ramp`, and `burst-recovery`. Local and calibration outputs are not
official benchmark results. See [Benchmark Contracts](docs/benchmark-contracts.md)
for contract ownership and catalog status.

The private trusted controller polls `main` and dispatches serial benchmark
campaigns to its home k3s runner. Complete valid evidence is published to the
append-only `benchmark-data` branch, with full raw evidence stored as a
checksummed GitHub Release asset. See [Continuous Benchmark
Automation](docs/automation.md) for the trust boundary and publication model.

## Public Dashboard

The static dashboard compares only complete run sets from the same cohort and
provides APM-style trial timelines for requested and achieved load, latency,
errors, CPU, and memory. Every deployment resolves the `benchmark-data` branch
to an exact commit, so the displayed dataset can be reproduced without a
database or backend service.

Run it locally:

```bash
make dashboard-dev
```

Validate tests, lint, and the production build:

```bash
make dashboard-check
```

The development server reads the mutable `benchmark-data` branch unless
`VITE_DATA_COMMIT` is set to an exact dataset commit.

## Requirements

- Docker
- Docker Compose
- k6
- Java 25 for local development
- kubectl access to context `homelab` for the official k3s profile

The Spring Boot implementation is generated from Spring Initializr. The Quarkus
implementation is generated from the official Quarkus generator and uses the
`3.33.2.1` LTS stream. Both include a Gradle wrapper and require Java 25. The
runner prefers local k6. If local k6 is unavailable, it can fall back to the
`grafana/k6` Docker image.

## Run

```bash
make run
```

Run the contract-defined number of independent trials while building the target
only once:

```bash
make run-set
```

Run on the frozen home k3s environment:

```bash
make run-set \
  ENVIRONMENT_PROFILE=home-k3s-v1 \
  MEASUREMENT_PROTOCOL=official-service-v1 \
  LOAD_PROFILE=platform-qualification-v1
```

Run one official core service cell:

```bash
make run-set \
  SCENARIO=transactional-command-api \
  ENVIRONMENT_PROFILE=home-k3s-v1 \
  MEASUREMENT_PROTOCOL=official-service-v1 \
  LOAD_PROFILE=steady
```

The k3s runner builds and pushes one `linux/amd64` image by default. A trusted
pipeline may pass an already published immutable image with `TARGET_IMAGE`. For
local qualification when registry push credentials are unavailable, use the
explicit `IMAGE_DISTRIBUTION=import` mode; it imports an OCI image into the fixed
k3s node before benchmark workloads start.

With explicit values:

```bash
make run IMPLEMENTATION=java/spring-boot SCENARIO=ping-api VARIANT=jvm-java25
```

Run a Quarkus core scenario with the same service contract:

```bash
make run \
  IMPLEMENTATION=java/quarkus \
  SCENARIO=transactional-command-api \
  VARIANT=jvm-java25
```

With explicit profile selections:

```bash
make run \
  LOAD_PROFILE=development-local \
  ENVIRONMENT_PROFILE=local-docker-compose \
  MEASUREMENT_PROTOCOL=development-service \
  BUILD_PROFILE=local-gradle-docker
```

The runner accepts the same selections as flags after the two required positional
arguments and optional variant:

```bash
PYTHONPATH=runner uv run --project runner python -m hrw_runner \
  java/spring-boot ping-api jvm-java25 \
  --load-profile development-local \
  --environment-profile local-docker-compose \
  --measurement-protocol development-service \
  --build-profile local-gradle-docker
```

Cold start scenario:

```bash
make run SCENARIO=cold-start-api
```

Transactional command scenario:

```bash
make run SCENARIO=transactional-command-api
```

Read-heavy query scenario:

```bash
make run SCENARIO=read-heavy-query-api
```

Its deterministic 100,000-row PostgreSQL dataset is initialized before the
target starts. Contract `1.0` freezes its home-k3s-calibrated base rate at 300
requests per second and enables the frozen official open-model profiles.

I/O aggregation scenario:

```bash
make run SCENARIO=io-aggregation-api
```

I/O aggregation timeout scenario:

```bash
make run SCENARIO=io-aggregation-timeout-api
```

Spring Boot virtual thread variant:

```bash
make run SCENARIO=io-aggregation-timeout-api VARIANT=jvm-java25-virtual-threads
```

The shorter `spring-boot` form is kept as a compatibility alias for the default Spring Boot JVM variant.

The Makefile calls the uv-managed Python runner. Before starting measurement, the runner resolves and strictly validates an exact-run manifest against the current checkout. It then uses the manifest's ordered Compose assets, cleans previous containers, builds the app and target image, measures startup, runs warmup and benchmark k6 phases when enabled, collects Docker stats, writes results, and shuts down the container.

`make run` keeps the original single-result development workflow. `make run-set`
is the repeatable evidence workflow: it builds once, executes the selected
measurement protocol's `trials`, resets Compose state between trials, and validates
the complete digest chain before returning successfully.
When `home-k3s-v1` is selected, the same command dispatches to the Kubernetes
runner. See [Home k3s Platform](docs/k3s.md) for the frozen host contract,
resources, validity rules, and image delivery modes.

The implementation contract owns the default variant and build profile. Each
service scenario owns the default load profile, environment profile, and
measurement protocol. The runner reads these contracts from:

```text
implementations/java/spring-boot/implementation.yaml
implementations/java/spring-boot/variants/jvm-java25.yaml
implementations/java/spring-boot/variants/jvm-java25-virtual-threads.yaml
implementations/java/quarkus/implementation.yaml
implementations/java/quarkus/variants/jvm-java25.yaml
scenarios/ping-api/scenario.yaml
scenarios/cold-start-api/scenario.yaml
scenarios/transactional-command-api/scenario.yaml
scenarios/read-heavy-query-api/scenario.yaml
scenarios/io-aggregation-api/scenario.yaml
scenarios/io-aggregation-timeout-api/scenario.yaml
contracts/load-profiles/
contracts/environment-profiles/
contracts/measurement-protocols/
contracts/build-profiles/
```

CLI flags and the corresponding Make variables can override all four profile
selections. Every run validates the complete contract repository first and rejects
selected draft profiles before execution. The current local runner still obtains
timing and VU values from each scenario's `load` section through the
`development-local` profile. The ownership model and validation rules are
documented in [docs/benchmark-contracts.md](docs/benchmark-contracts.md).
The resolved selections, effective execution configuration, input assets, and Git
provenance are documented in [docs/resolved-run-manifest.md](docs/resolved-run-manifest.md).

Spring Boot and Quarkus implement the same request and response behavior for all
three core scenarios. They preserve the same transaction/outbox semantics for
`transactional-command-api`, indexed keyset pagination for
`read-heavy-query-api`, and outbound-client behavior for `io-aggregation-api`.
Moving the official target image repository from the
Spring-specific environment profile to each implementation contract changed
`home-k3s-v1` to contract version `1.2`; results under that version form a new
comparison cohort and must not be merged with earlier environment versions.

Scenario details for humans live in each scenario directory:

```text
scenarios/ping-api/README.md
scenarios/cold-start-api/README.md
scenarios/transactional-command-api/README.md
scenarios/read-heavy-query-api/README.md
scenarios/io-aggregation-api/README.md
scenarios/io-aggregation-timeout-api/README.md
```

## Results

Each run creates a timestamped directory:

```text
results/java/spring-boot/jvm-java25/<scenario>/2026-07-04T21-30-00_java_spring-boot_jvm-java25_<scenario>/
├── resolved-manifest.json
├── metadata.json
├── build.json
├── startup.json
├── k6-summary.json
├── docker-stats.json
├── result.json
└── run.log
```

Each run set creates shared build and manifest evidence plus independent trial
directories:

```text
results/<language>/<framework>/<variant>/<scenario>/<run_set_id>/
├── resolved-manifest.json
├── metadata.json
├── build.json
├── dataset-preflight.json  # read-heavy-query-api only
├── run-set.json
├── run.log
└── trials/
    └── 01/
        ├── trial.json
        ├── result.json
        ├── time-series.json
        ├── artifact-manifest.json
        ├── startup.json
        ├── correctness.json
        ├── k6-summary.json
        ├── docker-stats.json
        ├── target.log
        └── run.log
```

The evidence document relationships and cross-file checksum rules are documented
in [docs/evidence-model.md](docs/evidence-model.md).

Validate and publish a completed official run set into a local dataset checkout:

```bash
make publish \
  RUN_SET_DIR=results/.../<run-set-id> \
  DATASET_DIR=../benchmark-data \
  SOURCE_COMMIT=<full-git-sha>
```

Automated publication additionally supplies the workflow URL and the raw
evidence archive URL and SHA-256. The publisher rejects local, partial, dirty, or
otherwise non-official evidence.

Some schema fields may be `null` during the MVP. The important contract is that result files are stable, timestamped, and machine-readable where practical.

Results mirror the implementation layout:

```text
results/<language>/<framework>/<variant>/<scenario>/<run_id>/
```

The normalized `result.json` shape is documented in [docs/results-schema.md](docs/results-schema.md). `resolved-manifest.json`, `metadata.json`, and `result.json` share the same exact-run digest and cohort fingerprint.

Summarize local benchmark results:

```bash
make summarize
make summarize-latest
make summarize-json
make summarize-latest-json
```

`make summarize` prints a compact table from `results/**/result.json`. `make summarize-json` emits the same rows as JSON for later static dashboard tooling.
Use the `latest` variants to keep only the newest run for each scenario, implementation, and variant combination.

## Local App

To run the app with Gradle:

```bash
cd implementations/java/spring-boot
./gradlew bootRun
curl http://localhost:8080/ping
```

Expected response:

```json
{"message":"pong"}
```

## Warning

Do not use early MVP output as a general-purpose performance conclusion. The first milestone is about runner correctness and repeatability.

## Runner Development

Validate all benchmark contracts:

```bash
make validate-contracts
```

Run the complete project checks:

```bash
make check
```

This validates the benchmark contracts, then runs the Python runner tests, the
Spring Boot tests in a Java 25 Docker image, and the Quarkus tests with the local
Java 25 runtime.
