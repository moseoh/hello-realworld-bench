# Continuous Benchmark Automation

The continuous benchmark pipeline separates untrusted validation, trusted
measurement, compact publication, and raw evidence storage.

## Trust Boundary

`.github/workflows/ci.yml` runs pull request checks on GitHub-hosted runners. It
has read-only repository access and never targets the home runner.

The public `.github/workflows/official-benchmark.yml` is a reusable worker with
only a `workflow_call` entry point. Scheduling and manual dispatch live in the
private `moseoh/hello-realworld-bench-ops` control repository. That repository
polls the public `main` branch, skips documentation-only commits, records the
last processed commit, and calls the worker with an exact full commit SHA.

The home runner is registered only to the private control repository. A public
pull request can add or change workflows in this repository, but it cannot
submit a job to a runner owned by the private repository. This repository must
never receive a repo-scoped self-hosted runner.

The jobs use separate permissions:

- `build` creates one `linux/amd64` OCI archive on a GitHub-hosted runner;
- `benchmark` receives read-only access and imports that archive into k3s; and
- `publish` runs on GitHub-hosted infrastructure and receives the public
  repository publication token only after measurement succeeds.

The target OCI archive is transferred through a short-lived Actions artifact.
No registry or publication credential is present on the home runner or inside a
benchmark namespace.

## Home Runner

The private control repository runner is named `homlab` and has the custom label
`hrw-home-k3s`. Its required tools are:

- Git;
- curl;
- kubectl with context `homelab`; and
- network access to GitHub, GHCR, and the k3s API.

The worker installs uv for each job. Image building happens on a
GitHub-hosted runner, so Docker, Java, Gradle, and make are not required on the
home runner.

The runner process lives in `~/actions-runner`. Install it as a system
service on the home host so it survives logout and reboot:

```bash
cd ~/actions-runner
sudo ./svc.sh install moseoh
sudo ./svc.sh start
```

The kubeconfig is stored at `/home/moseoh/.kube/config` with mode `0600`. The
worker sets `KUBECONFIG` explicitly. Runner registration or kubeconfig
replacement must be performed through a trusted administrator session. The
private repository stores the `PUBLIC_REPO_TOKEN` secret used only by the
GitHub-hosted publication job.

## Publication

The `publish` runner command validates the complete local evidence chain before
promotion. It accepts only run sets that are:

- complete with every expected trial present and valid;
- produced from a clean checkout of the trusted source commit;
- produced by `home-k3s-v1` and `official-service-v1`; and
- backed by an immutable target image digest.

Compact public data is appended to the `benchmark-data` branch:

```text
catalog.json
run-sets/<cohort-fingerprint>/<run-set-id>/
├── publication.json
├── run-set.json
├── resolved-manifest.json
├── build.json
├── preflight.json
├── postflight.json
└── trials/<index>/
    ├── trial.json
    ├── result.json
    ├── time-series.json
    └── artifact-manifest.json
```

An existing run-set path cannot be changed. Every publication revalidates all
cataloged publication manifests and compact file hashes. Re-publishing identical
bytes is idempotent; any content conflict fails publication. `catalog.json` is
sorted and contains source, image, cohort, selection, and publication-manifest
provenance.

The complete run-set directory is also published idempotently as a run-specific GitHub
Release asset named `raw-evidence.tar.gz`. Its SHA-256 file is a second asset,
and the compact `publication.json` records both the public asset URL and digest.
The Actions artifact is only the job-to-job transport and a 90-day operational
copy.

## Current Campaign

The trusted worker currently builds one immutable target image for every
supported implementation, then each cell selects its implementation-keyed
artifact for the serial home-runner matrix. The first core campaign contains
`transactional-command-api` and `io-aggregation-api`, each under `steady`,
`capacity-ramp`, and `burst-recovery`. Every cell contains three trials. Publish
jobs are also serialized so append-only catalog updates cannot race.

`ping-api` remains a separate platform qualification cell and is not a backend
performance conclusion.

`read-heavy-query-api` is implemented for both targets and allowlisted by the
worker, but it is not part of the private campaign matrix while its scenario
contract is marked uncalibrated. Promotion requires a home-k3s calibration run,
a frozen arrival rate, and a scenario contract version change.
