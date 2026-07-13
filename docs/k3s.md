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

## Validity

Preflight verifies the context, node identity, Ready state, architecture,
capacity, CPU-manager policy, kubelet stats endpoint, and absence of an older
benchmark namespace. Preflight, measured-window, and postflight evidence records
host CPU and memory.

The v1 background ceilings are 2,000 millicores and 8,000,000,000 working-set
bytes. The CPU allowance accounts for the timestamp skew between kubelet node and
container samples while still rejecting material contention on the 16-thread
host. Resource samples are expected every ten seconds with at least 90 percent
coverage. A failed correctness check is application-invalid; orchestration,
resource, noise, or evidence failures are infrastructure-invalid. Invalid trials
remain in the run set but do not contribute to promoted summaries.

## Images

The target image must be
`ghcr.io/moseoh/hello-realworld-bench/spring-boot@sha256:<digest>` for
`linux/amd64`. k6 is pinned to its architecture-specific manifest digest.

The default image mode pushes to GHCR. `TARGET_IMAGE` reuses an immutable image
already built by a trusted pipeline. `IMAGE_DISTRIBUTION=import` is a local
qualification fallback: a temporary privileged loader imports an OCI archive into
k3s containerd, is deleted before target startup, and is never part of a measured
trial.

## Protocol

`official-service-v1` fixes three trials, a 120-second warmup, and a 480-second
measured window. `platform-qualification-v1` fixes the closed-model traffic used
to validate the runner; it is not a backend performance conclusion. The official
environment rejects other protocol or load-profile combinations so a one-trial
development run cannot be mistaken for official platform evidence.
