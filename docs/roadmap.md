# Roadmap

## Product Goal

Hello Real World Bench will be a public benchmark product that produces auditable comparisons of backend runtime trade-offs under practical service conditions.

Given a trusted source commit, the platform should:

1. build each implementation and immutable target image once;
2. execute versioned benchmark contracts on a controlled k3s host;
3. collect repeatable summary and time-series evidence;
4. publish validated results as an immutable GitHub-backed dataset; and
5. render comparison and run-detail views without requiring a backend service.

The project does not produce universal runtime rankings. A result is comparable only within the same service scenario, load profile, environment profile, and measurement protocol.

## Current Baseline

The current repository is an experimental prototype baseline, not an official result producer.

It already provides:

- a uv-managed Python runner;
- Docker Compose execution;
- Spring Boot 4 and Quarkus `3.33.2.1` LTS reference implementations on Java 25;
- JVM and Spring virtual-thread variants;
- `ping-api`, `cold-start-api`, `transactional-command-api`, `io-aggregation-api`, and `io-aggregation-timeout-api`;
- build, startup, k6, and Docker resource measurements; and
- normalized local result JSON.
- a frozen home k3s platform with three-trial and time-series evidence;
- trusted-main GitHub Actions automation; and
- append-only compact dataset and checksummed raw-evidence publication.

`ping-api` remains a runner smoke test. Existing local results remain development evidence and must not be promoted as official benchmark data.

The automated `ping-api` qualification campaign validates the official platform
and publication path. Core scenario campaigns remain unfinished, so the project
does not yet have a comparative public dataset.

Environment contract version `1.2` starts a new comparison cohort because target
image repository ownership moved from the Spring-specific environment profile to
each implementation contract. Transactional scenario version `1.2` also raises
pre-allocated VUs from 100 to 200 after a 1,000 requests/second burst showed a
113 ms tail and four dropped iterations while dynamic allocation caught up.

## Benchmark Model

The benchmark model keeps independent concerns separate:

- **implementation**: source and framework programming model;
- **variant**: build or runtime choice within an implementation;
- **service scenario**: practical work performed by the target;
- **load profile**: traffic shape applied to a service scenario;
- **environment profile**: host, orchestration, resources, placement, and validity rules;
- **trial**: one timed execution under a fully resolved contract;
- **run set**: three trials under identical conditions; and
- **dataset release**: validated run sets published together as an immutable cohort.

Build measurements, lifecycle measurements, and service runtime measurements are separate evidence families:

- build profiles measure clean build, rebuild, and image packaging conditions;
- `cold-start-api` measures repeated time to first successful business response; and
- service scenarios run traffic profiles and collect latency, throughput, errors, and resource time series.

An official service trial uses a two-minute warmup followed by an eight-minute measured window. Each comparison cell contains three independent trials. A longer soak profile is a later extension, not part of the default matrix.

The first load profile suite is:

- `steady`: constant arrival rate;
- `capacity-ramp`: increasing arrival rate through a fixed sequence of stages; and
- `burst-recovery`: deterministic spikes followed by recovery windows.

Load rates are calibrated once per service scenario and then remain identical across comparable implementations. Random traffic must use a fixed seed or a deterministic schedule.

## Milestone 1: Benchmark Contract v1

Define the contracts that make results comparable before producing official data.

Scope:

- version and digest every implementation, variant, scenario, load profile, environment profile, and measurement protocol;
- resolve those inputs into an immutable run manifest;
- define comparison cohort rules and validity outcomes;
- define trial, run-set, summary, compact time-series, and raw-artifact manifests;
- define correctness oracles and state-reset requirements for each scenario;
- separate build cache profiles from runtime scenarios; and
- specify provenance required to reproduce or audit a result.

Exit criteria:

- all official configuration and result examples pass machine validation;
- incompatible results cannot be placed in the same comparison cohort;
- every summary value can be traced to a trial and its raw evidence; and
- contract changes create an explicit version change instead of silently altering meaning.

## Milestone 2: Official Measurement Platform v1

Add a controlled k3s execution profile while retaining Docker Compose for local development and contract checks.

Scope:

- version the single-node home k3s environment as an official environment profile;
- reserve resources for the host and k3s control plane;
- isolate target, dependency, and k6 CPU allocation where the host topology permits it;
- use fixed CPU and memory requests and limits for each benchmark role;
- perform preflight, in-run, and postflight host validity checks;
- build an implementation image once and reuse its digest across the run matrix;
- execute three-trial run sets with deterministic ordering and state reset;
- collect aligned target, dependency, and load-generator time series; and
- distinguish application outcomes from infrastructure-invalid trials.

Role-specific CPU and memory allocations are finalized and frozen only after inspecting the host topology and confirming that the load generator and dependencies do not become unintended bottlenecks.

Exit criteria:

- the same resolved manifest produces complete run sets without manual correction;
- measured windows have sufficient time-series coverage under the frozen v1 methodology;
- no benchmark workloads or mutable state remain after cleanup;
- image, host, runtime, tool, and contract provenance is complete; and
- an environment profile change starts a new comparison epoch.

## Milestone 3: Existing Scenario Qualification

Use the Spring Boot baseline and the two existing core service patterns to qualify the measurement platform.

Scope:

- run `transactional-command-api` under all three load profiles;
- run `io-aggregation-api` under all three load profiles;
- verify scenario correctness after every trial;
- verify that k6, PostgreSQL, and WireMock are not unintended bottlenecks;
- rotate run order to expose thermal or temporal bias; and
- calibrate and freeze the v1 repeatability thresholds.

Exit criteria:

- every comparison cell produces three valid trials;
- repeated unchanged campaigns satisfy the frozen repeatability thresholds;
- dependency saturation and load-generator saturation are detected rather than attributed to the target; and
- the methodology can classify failures without selecting only favorable trials.

## Milestone 4: Continuous Benchmark Dataset

Automate trusted execution and immutable publication before increasing the implementation matrix.

Scope:

- run normal CI checks for pull requests on GitHub-hosted runners;
- allow the home benchmark host to execute only trusted `main` commits and maintainer full-run requests;
- prevent public pull-request workflows from reaching home-runner credentials;
- calculate an impacted matrix from the last successful official commit;
- fall back to a full matrix for shared, global, or unclassified changes;
- publish summary and compact time-series files to a dedicated GitHub data target;
- store larger raw artifacts separately with immutable checksums; and
- promote only complete, validated run sets to the canonical catalog.

Exit criteria:

- documentation-only changes do not start benchmark campaigns;
- missed impact classification falls back to a full run rather than skipping coverage;
- failed or partial run sets never replace canonical data;
- published evidence is append-only and traceable to source and image digests; and
- the publication path has no long-lived write credential inside benchmark workloads.

## Milestone 5: Second Implementation and Fairness Gate

Add one contract-challenger implementation after the runner and official environment are stable. Its purpose is to reveal assumptions embedded by the first framework, not to produce a universal winner.

Scope:

- use the implementation provider's official generator and current LTS runtime;
- implement contracts independently rather than translating Spring Boot source;
- support `transactional-command-api` and `io-aggregation-api` first;
- share only scenario contracts, datasets, dependencies, and load profiles;
- document idiomatic defaults and all non-default tuning; and
- revise ambiguous contracts and revalidate both implementations when differences are found.

Exit criteria:

- both implementations pass the same correctness contracts;
- `2 implementations x 2 service scenarios x 3 load profiles x 3 trials` produces 36 valid trials;
- each comparison uses equivalent work, dependency behavior, resources, and measurement rules; and
- framework-specific optimizations appear only as documented variants.

The Quarkus implementation and two-implementation workflow are present, but the
36-trial qualification campaign has not been completed. This milestone remains
open until every required trial is valid.

## Milestone 6: Core Scenario Suite v1

Complete the smallest representative service-pattern set after the second implementation has challenged the contracts.

Core service scenarios:

- `transactional-command-api`: validation, domain logic, one database transaction, and one outbox insert;
- `read-heavy-query-api`: indexed database reads, fixed query mix, pagination, and bounded JSON responses; and
- `io-aggregation-api`: parallel upstream HTTP calls and response aggregation.

`read-heavy-query-api` excludes application caching in v1. Its dataset, schema, indexes, selectivity, pagination, response size, and cache-temperature policy must be fixed by the scenario contract.

Exit criteria:

- both baseline implementations support all three core scenarios;
- the baseline cohort contains `2 implementations x 3 scenarios x 3 load profiles x 3 trials`, or 54 valid trials;
- `cold-start-api` and build profiles publish separate comparable cohorts; and
- the first complete v1 dataset release can be regenerated from its manifests and raw evidence.

## Milestone 7: Public Static Web

Build the public product only after a complete comparative dataset exists.

Scope:

- provide a summary comparison view for compatible cohorts;
- provide an APM-style run detail view over the normalized trial timeline;
- show requested and achieved load, latency percentiles, errors, CPU, and memory;
- expose environment, methodology, source, image, and artifact provenance;
- prevent incompatible cohorts from being overlaid or ranked together; and
- consume static GitHub-backed data without a required database or backend API.

Exit criteria:

- a dataset release can be pinned and rendered deterministically;
- summary values match the canonical run-set data;
- detail charts align warmup, measured phases, and load stages correctly; and
- every presented conclusion is scoped to its scenario and benchmark conditions.

## Milestone 8: Controlled Expansion

Expand one comparison axis at a time after the v1 product is complete.

Candidate extensions:

- JVM, native image, garbage collector, and virtual-thread variants;
- additional implementations such as Go;
- timeout and degraded-dependency scenarios;
- soak profiles;
- observability overhead as paired uninstrumented and instrumented runs;
- event-processing workers;
- file-streaming APIs;
- Redis-backed read variants; and
- additional versioned execution environments, including serverless profiles.

OpenTelemetry, Redis, Kafka or Redpanda, additional runtimes, and Kubernetes variants must not be added to the core v1 matrix merely to increase breadth. Each extension needs a clear benchmark question, a frozen contract, and a complete comparable cohort.

An AI maintainability benchmark may be added later as a separate evidence family. It should apply the same change request to each implementation and evaluate test feedback, architecture constraints, and implementation quality without mixing those results into runtime performance cohorts.

A database or backend API for result browsing should be introduced only when static partitioning and lazy loading are demonstrably insufficient. GitHub evidence remains the canonical source; any database is a rebuildable query index.

## Delivery Model

Milestones are implemented sequentially because later work depends on earlier contracts. Independent work inside an active milestone may use isolated worktrees and agents when file ownership does not overlap.

Each milestone follows the same delivery gate:

1. create a scoped implementation plan;
2. implement with focused tests;
3. verify the full affected system;
4. remove temporary planning artifacts;
5. open and merge a concise pull request; and
6. update `main` before starting the next milestone.
