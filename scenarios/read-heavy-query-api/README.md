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
verified end to end. The provisional `0.3` contract deliberately sets
`arrival_rate.calibrated: false`; frozen official open-model profiles reject it
until the rate is calibrated on the home k3s environment.

The provisional base rate is 300 requests per second. Calibration runs the
short `calibration-steady` and `calibration-burst` profiles against both
implementations; the burst reaches five times the base rate. Calibration
evidence is retained for review but is never published as an official result.
