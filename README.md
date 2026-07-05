# Hello Real World Bench

Backend runtime benchmarks beyond Hello World.

Hello Real World Bench compares backend runtimes and frameworks using practical service patterns such as transactional APIs, I/O aggregation, cold starts, and build/startup metrics.

The project starts small: one implementation, one scenario, and one repeatable benchmark runner.

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

Supported implementation:

- `java/spring-boot` with the `jvm-java25` variant

Supported scenarios:

- `ping-api`
- `cold-start-api`
- `transactional-command-api`
- `io-aggregation-api`
- `io-aggregation-timeout-api`

The `ping-api` scenario exists to validate benchmark runner automation. It is not a meaningful real-world performance conclusion by itself.

The `cold-start-api` scenario measures repeated time to first successful `/ping` response after the application starts. It does not model serverless platform cold starts.

## Requirements

- Docker
- Docker Compose
- k6
- Java 25 for local development

The Spring Boot implementation is generated from Spring Initializr and includes the Gradle wrapper. The runner prefers local k6. If local k6 is unavailable, it can fall back to the `grafana/k6` Docker image.

## Run

```bash
make run
```

With explicit values:

```bash
make run IMPLEMENTATION=java/spring-boot SCENARIO=ping-api VARIANT=jvm-java25
```

Cold start scenario:

```bash
make run SCENARIO=cold-start-api
```

Transactional command scenario:

```bash
make run SCENARIO=transactional-command-api
```

I/O aggregation scenario:

```bash
make run SCENARIO=io-aggregation-api
```

I/O aggregation timeout scenario:

```bash
make run SCENARIO=io-aggregation-timeout-api
```

The shorter `spring-boot` form is kept as a compatibility alias for the default Spring Boot JVM variant.

The Makefile calls the uv-managed Python runner. The runner cleans previous containers, builds the app, builds the target image, starts Docker Compose, waits for the scenario endpoint to return 200, runs warmup and benchmark k6 phases when enabled, collects Docker stats, writes results, and shuts down the container.

The runner reads scenario and variant metadata from:

```text
scenarios/ping-api/scenario.yaml
scenarios/cold-start-api/scenario.yaml
scenarios/transactional-command-api/scenario.yaml
scenarios/io-aggregation-api/scenario.yaml
scenarios/io-aggregation-timeout-api/scenario.yaml
implementations/java/spring-boot/variants/jvm-java25.yaml
```

Scenario details for humans live in each scenario directory:

```text
scenarios/ping-api/README.md
scenarios/cold-start-api/README.md
scenarios/transactional-command-api/README.md
scenarios/io-aggregation-api/README.md
scenarios/io-aggregation-timeout-api/README.md
```

## Results

Each run creates a timestamped directory:

```text
results/java/spring-boot/jvm-java25/<scenario>/2026-07-04T21-30-00_java_spring-boot_jvm-java25_<scenario>/
├── metadata.json
├── build.json
├── startup.json
├── k6-summary.json
├── docker-stats.json
├── result.json
└── run.log
```

Some schema fields may be `null` during the MVP. The important contract is that result files are stable, timestamped, and machine-readable where practical.

Results mirror the implementation layout:

```text
results/<language>/<framework>/<variant>/<scenario>/<run_id>/
```

The normalized `result.json` shape is documented in [docs/results-schema.md](docs/results-schema.md).

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

```bash
make check
```

This runs the Python runner tests and the Spring Boot tests in a Java 25 Docker image.
