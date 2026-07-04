# Scenarios

Scenarios are service-pattern based. They are not named after a single technology.

Each scenario must define:

```text
name
question
what this measures
what this does not measure
dependencies
variants
metrics
```

## ping-api

Name:

```text
ping-api
```

Question:

```text
Can the benchmark runner build, start, load test, collect metrics, and save results for a target implementation?
```

What this measures:

- runner automation correctness
- basic HTTP request handling under a short load profile
- startup readiness timing
- build and Docker image build timing
- basic Docker resource snapshot collection

What this does not measure:

- database performance
- cache behavior
- message processing
- external HTTP aggregation
- file streaming
- observability overhead
- real transactional application behavior

Dependencies:

- target HTTP service only

Variants:

- none in the MVP

Metrics:

- build time
- Docker image build time
- startup ready time
- first request latency
- request rate
- p50 latency
- p95 latency
- p99 latency
- error rate
- CPU snapshot
- memory snapshot

## Future Scenario Notes

Future scenarios may include:

- `transactional-command-api`
- `read-heavy-query-api`
- `io-aggregation-api`
- `event-processing-worker`
- `file-streaming-api`
- `cold-start-api`
- `observability-overhead-api`
