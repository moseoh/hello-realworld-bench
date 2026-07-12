# Benchmark Contracts

Hello Real World Bench separates service behavior, implementation choices, and
measurement conditions into versioned YAML contracts. This keeps a scenario
independent from a language or framework and makes the inputs to a run explicit.

Contract validation checks schema conformance, path and identifier consistency,
unique identities, and references between documents. It does not prove that a
benchmark run is reproducible, fair, or suitable for a performance conclusion.

## Ownership Model

| Contract | Location | Owns |
| --- | --- | --- |
| Implementation | `implementations/<language>/<framework>/implementation.yaml` | Language, framework, programming model, and default variant. |
| Variant | `implementations/<language>/<framework>/variants/<variant>.yaml` | A runtime and container configuration for one implementation. |
| Service scenario | `scenarios/<scenario>/scenario.yaml` | Technology-neutral service behavior, dependencies, target endpoint, measured and excluded concerns, service conditions, metrics, and references to measurement profiles. |
| Load profile | `contracts/load-profiles/<profile>.yaml` | Load model, executor, timing source, and load phases. |
| Environment profile | `contracts/environment-profiles/<profile>.yaml` | Orchestrator, load-generator placement, and whether the environment is official. |
| Measurement protocol | `contracts/measurement-protocols/<protocol>.yaml` | Evidence family, trial count, warmup, and measured duration. |
| Build profile | `contracts/build-profiles/<profile>.yaml` | Build tool, dependency-cache state, image-cache state, and image input. |

A service scenario does not select an implementation, runtime, or implementation
variant. The command selects the implementation and may select a variant. When a
variant is omitted, the implementation contract supplies its default. The service
scenario selects one load, environment, measurement, and build profile by stable
ID.

## Current Catalog Status

The current executable path is local development only:

- `development-local` is a development load profile. The runner continues to
  read duration and VU values from each scenario's `load` section.
- `local-docker-compose` is a development environment profile with
  `official: false`; the target and load generator run on the same host.
- `development-service` and `cold-start` are development measurement protocols.
- `local-gradle-docker` is a development build profile with persistent Gradle
  dependencies and enabled Docker layer caching.
- `none` is the frozen disabled-load profile used by lifecycle measurements.

The `steady`, `capacity-ramp`, and `burst-recovery` load profiles are draft
catalog definitions for future official-profile work. The current runner does
not execute these draft profiles. They are not official benchmark profiles, and
results produced by the current local runner are not official benchmark results.

## Validation

Validate every contract in the repository:

```bash
make validate-contracts
```

The underlying command is:

```bash
PYTHONPATH=runner uv run --project runner python -m hrw_runner validate
```

Successful validation prints the number of discovered contract files. Invalid
repositories return exit code `1` and print all discovered validation errors to
standard error. `make check` runs contract validation before the runner and
Spring Boot test suites.
