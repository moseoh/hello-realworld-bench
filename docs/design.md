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

The runner is a shell script entry point:

```bash
./scripts/run.sh spring-boot ping-api
```

It coordinates cleanup, build measurement, image build measurement, startup measurement, warmup, load test execution, Docker stats collection, and result persistence.

## Docker Compose

Docker Compose is the first execution profile. Kubernetes is intentionally out of scope for the MVP and should be added later as a separate profile.

The target service is always named `target` so scenarios can interact with a stable contract.

## Target Implementation

The first implementation is Spring Boot 3 with Java 21. It exposes:

- `GET /ping`
- `GET /actuator/health`

No database, cache, message broker, tracing stack, or service mesh is included in the MVP.

## Scenario Load Test

The first scenario is `ping-api`. It validates that the benchmark runner can exercise a target service and save outputs. It is not a final performance comparison.

## Result Output

Each run writes a timestamped directory under `results/`. Outputs are intended to be machine-readable where practical, with raw logs retained for debugging.

The first target is runner correctness, not performance ranking.
