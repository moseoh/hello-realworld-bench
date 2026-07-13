# Home k3s Platform

`home-k3s-v1` is the first official execution environment. It is a versioned
contract for one physical host, not a claim that all Kubernetes clusters behave
the same way.

## Frozen Host

- kube context: `homelab`
- node and machine identity: `homlab` / `f66cd2d134b94bb18eb7e531d1baf343`
- CPU: AMD Ryzen 7 5825U, 8 cores and 16 threads
- minimum memory: 29,313,151,795 bytes
- architecture: `linux/amd64`
- CPU manager: `none`

The profile uses CFS quota rather than exclusive CPU pinning. Changing to the
static CPU manager would create a separate environment contract and comparison
epoch.

## Resources

Requests equal limits for every measured role:

| Role | CPU | Memory |
| --- | ---: | ---: |
| Target | 2 | 1 GiB |
| Dependency | 1 | 1 GiB |
| k6 | 4 | 3 GiB |

One namespace is created per run set. Dependencies are prepared first, one target
image digest is reused across every trial, and target state is reset between
trials. The namespace is deleted on both success and failure.

PostgreSQL and WireMock each use the dependency allocation. PostgreSQL remains
running across a run set, but the transactional tables are truncated after each
warmup. The measured order, order-item, and outbox row counts must each match the
measured successful iteration count. WireMock is stateless with its request
journal disabled.

## Validity

Preflight verifies the context, node identity, Ready state, architecture,
capacity, CPU-manager policy, kubelet stats endpoint, and absence of an older
benchmark namespace. Preflight, measured-window, and postflight evidence records
host CPU and memory.

The v1 background ceilings are 2,000 millicores and 8,000,000,000 working-set
bytes. The CPU allowance accounts for the timestamp skew between kubelet node and
container samples while still rejecting material contention on the 16-thread
host. k6 may use at most 350 percent CPU from its four-core allocation, and a
dependency may use at most 95 percent of its one-core allocation. Resource
samples are expected every ten seconds with at least 90 percent
coverage. A failed correctness check is application-invalid; orchestration,
resource, noise, or evidence failures are infrastructure-invalid. Invalid trials
remain in the run set but do not contribute to promoted summaries.

## Images

The target image repository comes from the selected implementation contract.
Spring Boot uses
`ghcr.io/moseoh/hello-realworld-bench/spring-boot@sha256:<digest>` and Quarkus
uses `ghcr.io/moseoh/hello-realworld-bench/quarkus@sha256:<digest>`, both for
`linux/amd64`. k6 is pinned to its architecture-specific manifest digest.

The default image mode pushes to GHCR. `TARGET_IMAGE` reuses an immutable image
already built by a trusted pipeline. `IMAGE_DISTRIBUTION=import` is a local
qualification fallback: a temporary privileged loader imports an OCI archive into
k3s containerd, is deleted before target startup, and is never part of a measured
trial.

Continuous automation passes both `HRW_TARGET_IMAGE` and
`HRW_TARGET_IMAGE_ARCHIVE`. The runner verifies the official digest reference
and imports the prebuilt OCI archive without rebuilding it or exposing registry
credentials to the home host. The same imported digest is reused for all trials.

## Protocol

`official-service-v1` fixes three trials, a 120-second warmup, and a 480-second
measured window. `steady`, `capacity-ramp`, and `burst-recovery` are frozen open
arrival-rate profiles. Scenario contracts own their calibrated base rate;
profile contracts own the deterministic multipliers and timing. Burst transitions
use zero-second stages so 3x and 5x spikes are immediate rather than ramps.

`home-k3s-calibration`, `calibration-service`, and the calibration load profiles
provide a one-trial development path. Calibration evidence is never publishable.
`platform-qualification-v1` remains the closed-model smoke workload and is not a
backend performance conclusion.
