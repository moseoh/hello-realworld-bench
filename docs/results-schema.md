# Results Schema

`result.json` is the normalized machine-readable summary for one benchmark run. Raw files such as `startup.json`, `k6-summary.json`, `docker-stats.json`, and `run.log` remain available in the same result directory for debugging.

The MVP schema version is:

```json
{
  "schema_version": "0.1"
}
```

Schema changes that rename fields, move fields, or change field semantics should increment `schema_version`.

## Common Shape

Every `result.json` file must include these top-level fields:

```json
{
  "schema_version": "0.1",
  "run_id": "2026-07-04T21-30-00_java_spring-boot_jvm-java25_ping-api",
  "project": "hello-realworld-bench",
  "scenario": "ping-api",
  "implementation": "java/spring-boot",
  "variant": "jvm-java25",
  "runtime": {},
  "environment": {},
  "build": {},
  "startup": {},
  "runtime_metrics": {}
}
```

## Runtime

`runtime` records implementation and variant metadata. For the current Spring Boot JVM variant, it includes:

```json
{
  "language": "java",
  "java_version": "25",
  "framework": "spring-boot",
  "spring_boot_version": "4.1.0",
  "build_mode": "jvm",
  "native_image": false,
  "virtual_threads": false,
  "otel": false
}
```

## Environment

`environment` records host and runner context:

```json
{
  "os": "Darwin",
  "cpu": "unknown",
  "memory_gb": "unknown",
  "docker": true,
  "load_generator": "same-host"
}
```

## Build

`build` records build and image metrics:

```json
{
  "clean_build_ms": 1000,
  "docker_build_ms": 2000,
  "image_size_mb": 300.5,
  "cache": {
    "gradle_user_home": "implementation-local .gradle-cache",
    "gradle_dependency_cache": "persistent",
    "docker_build_cache": "enabled",
    "docker_build_input": "prebuilt application artifact"
  }
}
```

Values may be `null` only when a measurement could not be collected.

## Startup

`startup` records scenario endpoint first-success timing:

```json
{
  "ready_ms": 1200,
  "first_request_ms": 7,
  "iterations": 1,
  "summary": {
    "ready_ms": {
      "min": 1200,
      "median": 1200,
      "p95": 1200,
      "max": 1200
    },
    "first_request_ms": {
      "min": 7,
      "median": 7,
      "p95": 7,
      "max": 7
    }
  }
}
```

`ready_ms` means time from container start until the scenario endpoint first returns HTTP 200. It does not mean a framework health endpoint reported ready. `first_request_ms` is the latency of the successful request that completed that first-success check.

For single-start scenarios such as `ping-api`, `ready_ms` and `first_request_ms` are the first and only sample. For repeated startup scenarios such as `cold-start-api`, these fields keep the first sample for backward-friendly quick reads, while `summary` contains aggregate values across samples. Full sample data lives in `startup.json`.

## Runtime Metrics

`runtime_metrics` contains metrics collected during or immediately after scenario execution.

For load-test scenarios, k6 metrics are included:

```json
{
  "rps": 123.4,
  "p50_ms": 4.5,
  "p95_ms": 12.3,
  "p99_ms": 45.6,
  "error_rate": 0,
  "cpu_percent": 12.34,
  "memory_usage": "128.5MiB / 1GiB",
  "memory_percent": 12.55
}
```

For load-disabled scenarios such as `cold-start-api`, k6 metrics are omitted instead of written as `null`:

```json
{
  "cpu_percent": 12.34,
  "memory_usage": "128.5MiB / 1GiB",
  "memory_percent": 12.55
}
```

The corresponding `k6-summary.json` records:

```json
{
  "skipped": true,
  "reason": "load disabled for scenario"
}
```

## Scenario-Specific Data

Scenario-specific raw or detailed data should stay in dedicated files such as `startup.json` or `k6-summary.json`. Keep `result.json` as a compact normalized summary so repeated runs can be compared without parsing every raw artifact.
