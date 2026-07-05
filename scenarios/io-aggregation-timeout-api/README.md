# io-aggregation-timeout-api

`io-aggregation-timeout-api` models an aggregation request where one upstream dependency is slower than the target client's configured timeout.

## Question

How does the target behave when an aggregation request must return despite one slow upstream dependency?

## What This Measures

- HTTP client timeout behavior
- Fallback response overhead
- Aggregation latency when one upstream is slow
- Resource usage while handling upstream timeout pressure

## What This Does Not Measure

- Retry behavior
- Circuit breaker state machines
- Database transactions
- Real internet latency
- Virtual thread behavior

## Dependencies

- `mock-upstream`, implemented with WireMock

## Variants

Initial variant:

- Spring Boot MVC, JVM Java 25, regular platform threads
- inventory upstream is slower than the target timeout and should fall back
- low concurrency, so this scenario isolates timeout/fallback behavior instead of mock server saturation

Future variants:

- Spring Boot virtual threads

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
