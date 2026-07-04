# cold-start-api

## Question

How long does the target take to serve the first successful business endpoint response after the application starts?

## Role

`cold-start-api` measures application startup behavior using the Docker Compose MVP profile. In this project, cold start means the time from starting the target container until `/ping` first returns HTTP 200. The latency of that successful `/ping` request is recorded separately.

This scenario does not model serverless platform cold starts.

Framework health endpoints are intentionally not used for this scenario because they can warm different parts of different runtimes before the first business endpoint request.

## What This Measures

- repeated time-to-first-success timing for `/ping`
- latency of the first successful `/ping` request
- basic Docker resource snapshot collection after the final startup sample

## What This Does Not Measure

- sustained throughput
- warm request latency under load
- database performance
- serverless platform cold starts
- native image startup unless a native-image variant is added later

## Dependencies

- target HTTP service only

## Default Implementation

- `java/spring-boot`

## Default Variant

- `jvm-java25`

## Variants

- none in the MVP

## Metrics

- time-to-first-success samples
- first request latency samples
- min, median, p95, and max time to first success
- min, median, p95, and max first request latency
- CPU snapshot
- memory snapshot
