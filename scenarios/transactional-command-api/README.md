# transactional-command-api

## Question

How does the target behave when handling a command request with validation, domain logic, one database transaction, and an outbox insert?

## Role

`transactional-command-api` is the first stateful service-pattern benchmark. It models a common backend write path without adding cache, messaging, external HTTP, file IO, observability instrumentation, or cluster orchestration.

## What This Measures

- HTTP request parsing and validation
- domain total calculation for an order command
- persistence through PostgreSQL
- one database transaction per request
- order, order item, and outbox inserts
- latency and error rate under a short write-heavy load profile

## What This Does Not Measure

- read-heavy query performance
- cache behavior
- message broker throughput
- outbox publishing
- distributed transactions
- external service aggregation
- observability overhead

## Dependencies

- target HTTP service
- PostgreSQL

## Variants

- `baseline`: Single transaction with order and outbox writes.

## Official Load

The calibrated base arrival rate is `200 requests/second`. Official runs apply
the frozen `steady`, `capacity-ramp`, and `burst-recovery` profiles for three
trials each. Warmup writes are truncated before measurement. After measurement,
the orders, order items, and outbox events must each equal the successful k6
iteration count.

## Metrics

- build time
- Docker image build time
- startup first-success timing
- first request latency
- request rate
- p50 latency
- p95 latency
- p99 latency
- error rate
- CPU snapshot
- memory snapshot
