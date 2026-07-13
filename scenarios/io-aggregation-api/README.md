# io-aggregation-api

`io-aggregation-api` models a request that fans out to several upstream HTTP calls and combines the responses into one API response.

## Question

How does the target behave when a request performs multiple upstream HTTP calls and aggregates the results?

## What This Measures

- HTTP client overhead inside the target service
- parallel upstream request behavior
- JSON decoding and response composition
- resource usage while waiting on external HTTP dependencies

## What This Does Not Measure

- database transactions
- cache or message broker behavior
- real internet latency
- circuit breaker or retry behavior
- upstream timeout behavior

## Dependencies

- `mock-upstream`, implemented with WireMock

HTTP client and mock upstream baseline settings are documented in [../../docs/http-baseline.md](../../docs/http-baseline.md).

## Variants

- `baseline`: All upstream responses complete within the response timeout.

## Official Load

The calibrated base arrival rate is `80 requests/second`. The rate keeps the
one-core mock upstream below its saturation threshold through the 5x burst while
still exercising deterministic steady, capacity-ramp, and burst-recovery traffic.
The target allows 128 active and 128 pending blocking upstream operations, and
WireMock uses 128 container threads with a 512-request accept queue.

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
