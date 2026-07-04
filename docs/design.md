# Design

Hello Real World Bench is a small benchmark automation platform.

The MVP architecture is:

```text
runner
  ↓
docker compose
  ↓
target implementation
  ↓
scenario load test
  ↓
result JSON
```

## Runner

The runner is a uv-managed Python entry point wrapped by the top-level Makefile:

```bash
make run
```

It coordinates cleanup, build measurement, image build measurement, startup measurement, warmup, load test execution, Docker stats collection, and result persistence.

## Docker Compose

Docker Compose is the first execution profile. Kubernetes is intentionally out of scope for the MVP and should be added later as a separate profile.

The target service is always named `target` so scenarios can interact with a stable contract.

## Target Implementation

Implementation source is organized by language and framework:

```text
implementations/
  java/
    spring-boot/
```

The first implementation is Spring Boot 4 with Java 25. It exposes:

- `GET /ping`
- `GET /actuator/health`

No database, cache, message broker, tracing stack, or service mesh is included in the MVP.

## Variants

A variant is a build or runtime choice inside one implementation. For example, Java version, Spring Boot version, JVM mode, native image mode, or virtual threads can be represented as variants when the service code remains the same.

The first variant is:

```text
implementations/java/spring-boot/variants/jvm-java25.yaml
```

Create a separate implementation folder only when the implementation source or framework family changes, such as Spring MVC versus WebFlux, Spring Boot versus Quarkus, or Java versus Go.

## Scenario Load Test

The first scenario is `ping-api`. It validates that the benchmark runner can exercise a target service and save outputs. It is not a final performance comparison.

## Result Output

Each run writes a timestamped directory under `results/`. Outputs are intended to be machine-readable where practical, with raw logs retained for debugging.

Results mirror the implementation layout:

```text
results/java/spring-boot/jvm-java25/ping-api/<run_id>/
```

The first target is runner correctness, not performance ranking.
