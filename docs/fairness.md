# Fairness

Hello Real World Bench compares implementations through shared scenario contracts.

## Rules

- Use the same scenario contract.
- Use the same endpoint semantics.
- Use the same response structure.
- Use the same resource limits where possible.
- Use the same database or mock dependencies when a scenario requires them.
- Use the same load profile.
- Do not use framework-specific cheating.
- Document optimizations.
- Present results as trade-offs, not universal rankings.

## Endpoint Semantics

For a scenario to be comparable, each implementation must do the same work and return the same shape of response.

## Resource Limits

The MVP uses Docker Compose resource constraints. Later profiles may add stricter CPU and memory controls, but they should remain scenario-level configuration rather than implementation-specific tuning.

## Variants

Variants are allowed when they represent documented build or runtime choices inside the same implementation. A JVM build and a native image build can be variants of `java/spring-boot` if they serve the same endpoint contract.

Use a new implementation folder when the source structure or framework family changes, such as `java/spring-boot-webflux` or `java/quarkus`.

## Optimization Disclosure

Framework-specific optimizations are allowed only when documented and available for comparison. Hidden shortcuts that skip required scenario behavior are not allowed.
