# io-aggregation-timeout-api

`io-aggregation-timeout-api` models an aggregation request where one upstream dependency is slower than the target client's configured timeout.

## Question

How does the target behave when an aggregation request must return despite one slow upstream dependency?

## What This Measures

- HTTP client timeout behavior
- fallback response overhead
- aggregation latency when one upstream is slow
- resource usage while handling upstream timeout pressure

## What This Does Not Measure

- retry behavior
- circuit breaker state machines
- database transactions
- real internet latency

## Dependencies

- `mock-upstream`, implemented with WireMock

HTTP client and mock upstream baseline settings are documented in [../../docs/http-baseline.md](../../docs/http-baseline.md).

## Variants

- `slow-upstream-fallback`: One upstream exceeds the response timeout and returns a fallback.

## Metrics

- build time
- Docker image build time
- dependency readiness time
- startup readiness time
- first request latency
- request rate
- p50, p95, p99 latency
- error rate
- Docker CPU and memory usage
