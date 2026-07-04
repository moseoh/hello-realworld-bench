# Hello Real World Bench

Backend runtime benchmarks beyond Hello World.

Hello Real World Bench compares backend runtimes and frameworks using practical service patterns such as transactional APIs, I/O aggregation, cold starts, and build/startup metrics.

The project starts small: one implementation, one scenario, and one repeatable benchmark runner.

## What This Is

Hello Real World Bench is an experimental benchmark automation platform for backend runtimes and frameworks. It focuses on service-pattern scenarios and repeatable measurement workflows.

The first milestone validates the runner itself:

- build measurement
- Docker image build measurement
- startup readiness measurement
- k6 load execution
- Docker resource snapshot collection
- timestamped machine-readable result output

## What This Is Not

This is not a final performance ranking. It is not a replacement for large benchmark suites such as TechEmpower. It is not intended to claim that one runtime is universally faster than another.

Early results should be read only as trade-offs under the exact scenario, host, resource limits, and load profile used for that run.

## Current Status

Experimental MVP.

Supported implementation:

- Spring Boot 3 + Java 21

Supported scenario:

- `ping-api`

The `ping-api` scenario exists to validate benchmark runner automation. It is not a meaningful real-world performance conclusion by itself.

## Requirements

- Docker
- Docker Compose
- k6
- Java 21 for local development

The Spring Boot implementation is generated from Spring Initializr and includes the Gradle wrapper. The runner prefers local k6. If local k6 is unavailable, it can fall back to the `grafana/k6` Docker image.

## Run

```bash
./scripts/run.sh spring-boot ping-api
```

The command cleans previous containers, builds the app, builds the target image, starts Docker Compose, waits for health, runs warmup and benchmark k6 phases, collects Docker stats, writes results, and shuts down the container.

## Results

Each run creates a timestamped directory:

```text
results/2026-07-04T21-30-00_spring-boot_ping-api/
├── metadata.json
├── build.json
├── startup.json
├── k6-summary.json
├── docker-stats.json
└── run.log
```

Some schema fields may be `null` during the MVP. The important contract is that result files are stable, timestamped, and machine-readable where practical.

## Local App

To run the app with Gradle:

```bash
cd implementations/spring-boot
./gradlew bootRun
curl http://localhost:8080/ping
```

Expected response:

```json
{"message":"pong"}
```

## Warning

Do not use early MVP output as a general-purpose performance conclusion. The first milestone is about runner correctness and repeatability.
