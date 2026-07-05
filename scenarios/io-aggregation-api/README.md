# io-aggregation-api

`io-aggregation-api` models a request that fans out to several upstream HTTP calls and combines the responses into one API response.

## Question

How does the target behave when a request performs multiple upstream HTTP calls and aggregates the results?

## What This Measures

- HTTP client overhead inside the target runtime
- Parallel upstream request behavior
- JSON decode and response composition
- Resource usage while waiting on external HTTP dependencies

## What This Does Not Measure

- Database transactions
- Redis or Kafka behavior
- Real internet latency
- Circuit breaker or retry behavior
- Timeout variants
- Virtual thread behavior

## Dependencies

- `mock-upstream`, implemented with WireMock

## Variants

Initial variant:

- Spring Boot MVC, JVM Java 25, regular platform threads

Future variants:

- timeout behavior
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
