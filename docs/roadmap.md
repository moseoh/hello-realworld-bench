# Roadmap

## Phase 0: Benchmark Runner MVP

- Spring Boot only
- `ping-api` only
- build/startup/k6/result JSON

## Phase 1: Transactional Command API

- PostgreSQL
- order command
- transaction
- outbox insert

## Phase 2: I/O Aggregation API

- mock upstream server
- parallel external HTTP calls
- timeout variant
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
