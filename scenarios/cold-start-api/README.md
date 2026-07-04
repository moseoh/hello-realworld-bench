# cold-start-api

## Question

How long does the target take to become ready and serve the first response after the application starts?

## Role

`cold-start-api` measures application startup behavior using the Docker Compose MVP profile. In this project, cold start means the time from starting the target container until the app reports ready, plus the latency of the first scenario request immediately after readiness.

This scenario does not model serverless platform cold starts.

## What This Measures

- repeated container startup readiness timing
- first `/ping` request latency immediately after readiness
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

- startup ready time samples
- first request latency samples
- min, median, p95, and max startup ready time
- min, median, p95, and max first request latency
- CPU snapshot
- memory snapshot
