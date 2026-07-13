# Fairness

Hello Real World Bench compares implementations through shared scenario contracts.

## Rules

- Use the same scenario contract.
- Use the same endpoint semantics.
- Use the same response structure.
- Use the same resource limits where possible.
- Use the same database or mock dependencies when a scenario requires them.
- Use the same load profile.
- Use the same outbound HTTP client behavior for HTTP scenarios.
- Do not use framework-specific cheating.
- Document optimizations.
- Present results as trade-offs, not universal rankings.

## Endpoint Semantics

For a scenario to be comparable, each implementation must do the same work and return the same shape of response.

The Spring Boot 4 and Quarkus `3.33.2.1` LTS Java 25 implementations share the
same `transactional-command-api` and `io-aggregation-api` request, response,
transaction, outbox, and fallback contracts. Their framework code is independent;
only the observable work and dependencies are shared.

Cold-start scenarios should use the scenario business endpoint as the readiness signal. Framework health endpoints should not be used for cold-start measurement because they can warm different parts of different runtimes before the first business request.

Docker Compose health checks should not call benchmarked endpoints during startup measurement. The runner should perform endpoint polling from the host.

## Resource Limits

The MVP uses Docker Compose resource constraints. Later profiles may add stricter CPU and memory controls, but they should remain scenario-level configuration rather than implementation-specific tuning.

## HTTP Baseline

HTTP scenarios must keep target HTTP client behavior and mock upstream settings comparable across implementations. The current baseline is documented in [http-baseline.md](http-baseline.md).

Spring Boot and Quarkus use their idiomatic HTTP clients, but both preserve the
documented pool bounds, concurrency and pending-operation limits, timeout values,
disabled retries and circuit breakers, and inventory-only fallback.

## Comparison Cohorts

Environment contract version `1.2` moves the target image repository from the
Spring-specific environment profile to each implementation contract. This is a
new comparison cohort: evidence produced under earlier environment contract
versions must not be compared directly with v1.2 evidence.

Transactional scenario contract version `1.2` raised pre-allocated k6 VUs
from 100 to 200 after a 1,000 requests/second calibration burst showed a 113 ms
tail and four dropped iterations during dynamic allocation. This changes the
load-generator condition and therefore cannot be mixed with scenario v1.1.
Version `1.3` adds bounded 10-second request, failure, and latency evidence;
it starts a new cohort because the load script and recorded evidence changed.

I/O aggregation contract version `1.2` and provisional read-heavy contract
version `0.3` add the same bounded timeline evidence to their respective
cohorts.

## Build Conditions

Build results must document cache state and run order. Gradle or Maven dependency cache behavior is different from Docker layer cache behavior, so build metrics should keep application build time and image build time separate.

## Variants

Variants are allowed when they represent documented build or runtime choices inside the same implementation. A JVM build and a native image build can be variants of `java/spring-boot` if they serve the same endpoint contract.

Use a new implementation folder when the source structure or framework family changes, such as `java/spring-boot-webflux` or `java/quarkus`.

## Optimization Disclosure

Framework-specific optimizations are allowed only when documented and available for comparison. Hidden shortcuts that skip required scenario behavior are not allowed.
