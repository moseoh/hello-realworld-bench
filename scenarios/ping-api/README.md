# ping-api

## Question

Can the benchmark runner build, start, load test, collect metrics, and save results for a target implementation?

## Role

`ping-api` is a runner validation scenario. It is useful as a smoke test when changing the runner or adding a new implementation, but it should not be treated as a meaningful real-world performance conclusion.

## What This Measures

- runner automation correctness
- basic HTTP request handling under a short load profile
- startup readiness timing
- build and Docker image build timing
- basic Docker resource snapshot collection

## What This Does Not Measure

- database performance
- cache behavior
- message processing
- external HTTP aggregation
- file streaming
- observability overhead
- real transactional application behavior

## Dependencies

- target HTTP service only

## Default Implementation

- `java/spring-boot`

## Default Variant

- `jvm-java25`

## Variants

- none in the MVP

## Metrics

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
