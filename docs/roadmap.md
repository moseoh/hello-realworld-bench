# Roadmap

## Phase 0: Benchmark Runner MVP

- Spring Boot only
- `ping-api` only
- build/startup/k6/result JSON
- config-driven runner using scenario and variant YAML
- stable extraction for k6 and Docker stats metrics

Phase 0 should be considered complete when `make run` is stable and result JSON fields are consistent enough to compare repeated runs under the same conditions.

## Phase 1: Transactional Command API

- PostgreSQL
- order command
- transaction
- outbox insert

Design draft: [transactional-command-api](scenario-designs/transactional-command-api.md)

Runnable scenario: [transactional-command-api](../scenarios/transactional-command-api/README.md)

## Phase 2: I/O Aggregation API

- mock upstream server
- parallel external HTTP calls
- timeout variant
- virtual thread variant for Spring Boot

Runnable base scenario: [io-aggregation-api](../scenarios/io-aggregation-api/README.md)

Runnable timeout scenario: [io-aggregation-timeout-api](../scenarios/io-aggregation-timeout-api/README.md)

Remaining Phase 2 work:

- virtual thread variant for Spring Boot

## Phase 3: Add Second Implementation

- Quarkus or Go
- same scenario contract

## Phase 4: Resource/Observability Extensions

- OpenTelemetry overhead
- Redis-backed read-heavy API
- event worker with Redpanda/Kafka
- file streaming API

## Phase 5: Kubernetes Profile

- k3s/kind profile
- CPU/memory limits
- separate from Docker Compose profile

## Phase 6: AI Maintainability Benchmark

- same change request applied to each implementation
- measure test coverage, architecture rule violations, compile/test feedback
