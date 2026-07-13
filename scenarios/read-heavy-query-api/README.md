# read-heavy-query-api

## Question

How does the target behave when serving bounded, indexed product queries from a warm PostgreSQL dataset?

## Role

`read-heavy-query-api` measures indexed, read-only PostgreSQL queries with a
fixed product dataset, deterministic query mix, and keyset pagination. The
dataset is initialized before the target starts and remains immutable for the
full run set.

## What This Measures

- HTTP query parameter parsing and validation
- indexed PostgreSQL reads with category and price filters
- keyset pagination ordered by `price_cents`, then `id`
- bounded JSON response serialization
- latency and error rate under a read-heavy load profile

## What This Does Not Measure

- application cache behavior
- database write performance
- message broker throughput
- external service aggregation
- observability overhead

## Dataset and Query Contract

The PostgreSQL initializer creates 100,000 deterministic `catalog_products`
rows and the `(category, active, price_cents, id)` index. Queries select active
products from one fixed category and inclusive price window, ordered by
`price_cents ASC, id ASC`. They use a limit of 20 or 50 and return a nullable
keyset cursor when another row exists.

The load script sends three first-page requests for every continuation request
across all categories, four price windows, and both page sizes. Responses must
not exceed 16 KiB.

## Cache Policy

PostgreSQL initializes the immutable dataset before the target starts. Normal
warmup establishes warm database-cache evidence; the scenario does not permit
an application cache.

## Current Status

Spring Boot and Quarkus implement this contract, and the local Compose runner is
verified end to end. Contract `1.0` freezes a base rate of 300 requests per
second for the official open-model profiles.

The 2026-07-13 calibration used source commit
`17eba6259fd072e42b28259afce41e12b52f28aa`. Both implementations completed
18,001 steady requests at 300 requests per second and 35,999 burst requests at
up to 1,500 requests per second. All four trials had zero dropped iterations,
zero semantic failures, and zero HTTP errors. The observed p99 range was
1.13-1.29 ms for steady load and 1.59-1.70 ms for burst load. Calibration
evidence is development evidence and is not an official performance result.
