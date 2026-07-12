# Benchmark Contracts

Hello Real World Bench separates service behavior, implementation choices, and
measurement conditions into versioned YAML contracts. This keeps a scenario
independent from a language or framework and makes the inputs to a run explicit.

Contract validation rejects duplicate YAML mapping keys and checks schema
conformance, required contract locations, load-profile semantics, path and
identifier consistency, unique identities, and references between documents. It
does not prove that a benchmark run is reproducible, fair, or suitable for a
performance conclusion.

## Ownership Model

| Contract | Location | Owns |
| --- | --- | --- |
| Implementation | `implementations/<language>/<framework>/implementation.yaml` | Language, framework, programming model, default variant, and default build profile. |
| Variant | `implementations/<language>/<framework>/variants/<variant>.yaml` | A runtime and container configuration for one implementation. |
| Service scenario | `scenarios/<scenario>/scenario.yaml` | Technology-neutral service behavior, dependencies, target endpoint, measured and excluded concerns, service conditions, metrics, and default load, environment, and measurement profiles. |
| Load profile | `contracts/load-profiles/<profile>.yaml` | Load model, executor, timing source, and load phases. |
| Environment profile | `contracts/environment-profiles/<profile>.yaml` | Orchestrator, load-generator placement, and whether the environment is official. |
| Measurement protocol | `contracts/measurement-protocols/<protocol>.yaml` | Evidence family, trial count, warmup, and measured duration. |
| Build profile | `contracts/build-profiles/<profile>.yaml` | Build tool, dependency-cache state, image-cache state, and image input. |

Variant identifiers are unique within their owning implementation, so different
implementations may use the same variant identifier. Every other contract kind
has repository-wide identity by contract kind and identifier. Language and
framework identity comes only from the implementation contract; result metadata
combines those canonical values with the selected variant's runtime fields.

A service scenario does not select an implementation, runtime, or implementation
variant. The command selects the implementation and may override a variant or any
profile selection. When values are omitted, the implementation supplies the
default variant and build profile, while the service scenario supplies the default
load profile, environment profile, and measurement protocol. A service scenario
never references a build profile.

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

Run resolution rejects any selected profile whose status is `draft` before
benchmark execution. Profiles with `development` or `frozen` status are eligible
only when their semantics match the current local runner: scenario-driven
constant-VU or disabled load, the current service or lifecycle measurement timing,
same-host Docker Compose, and the current cached Gradle and Docker build path.
Other executable profile semantics are rejected instead of being ignored. The
`steady`, `capacity-ramp`, and `burst-recovery` load profiles are draft catalog
definitions for future official-profile work, not executable or official
benchmark profiles. Results produced by the current local runner are not official
benchmark results.

## Validation

Validate every contract in the repository:

```bash
make validate-contracts
```

The underlying command is:

```bash
PYTHONPATH=runner uv run --project runner python -m hrw_runner validate
```

Every immediate `scenarios/<scenario>/` directory must contain `scenario.yaml`,
and every `implementations/<language>/<framework>/` directory must contain
`implementation.yaml`. YAML outside the documented contract paths is not parsed as
a contract.

Path-derived identifiers and references use portable lowercase slugs. A slug
matches `[a-z0-9]+` segments separated only by single hyphens. Scenario, profile,
and variant identifiers, scenario variant identifiers, language and framework
values, and default variant and profile references all use this form.
Implementation identifiers and variant implementation references contain exactly
two slugs separated by one slash, as `<language>/<framework>`.

When scenario load is enabled, `load.script` must be a canonical POSIX
repository-relative path under `scenarios/<scenario-id>/`, may name a nested file,
and must end in `.js`. Absolute paths, backslashes, empty, dot, or dot-dot path
segments, duplicate separators, and paths that require normalization are invalid.
The path must identify an existing regular file, and its resolved path must remain
inside the owning scenario directory so that symlinks cannot escape that boundary.
Load-disabled scenarios do not require a script.

Successful validation prints the number of discovered contract files. Invalid
repositories return exit code `1` and print all parse, schema, semantic, path, and
reference errors in deterministic order to standard error. Run resolution performs
the same repository-wide validation before selecting a runnable configuration.
`make check` runs contract validation before the runner and Spring Boot test suites.
