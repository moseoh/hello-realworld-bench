# Resolved Run Manifest

Each service or lifecycle run writes `resolved-manifest.json` before startup or
load measurement begins. The manifest records the exact resolved inputs used by
the runner. Its `manifest_digest` is a SHA-256 digest of the complete manifest
payload except the digest field itself.

The exact-run payload includes:

- the run ID;
- the selected implementation, variant, scenario, and profiles;
- digests and repository paths for all selected contracts;
- the effective runtime, target, service, load, startup, Compose profile, and image settings;
- the Compose overlays and scenario files used as input assets, with SHA-256 digests;
- the Git commit, dirty state, and worktree digest.

`source.git_dirty` reports tracked modifications or untracked, non-ignored files. `source.worktree_digest` identifies the actual tracked and untracked checkout contents, so a dirty run is distinguishable from the clean commit even when `source.git_commit` is the same.

Build runs write `build-resolved-manifest.json` before cache seeding or timed
operations. Its exact-run payload includes the selected implementation and
variant contracts, the resolved source workspace and Docker inputs, the source
commit and tree digest, and the environment, measurement, and build profile contracts.
The implementation and variant remain exact-run identity but are not build cohort
identity.

## Cohort Fingerprint

The service and lifecycle cohort fingerprint identifies inputs that must match
for results to belong to the same comparison cohort. It includes:

- the measurement protocol evidence family;
- the scenario, load profile, environment profile, and measurement protocol contracts;
- environment and scenario Compose assets;
- scenario input files.

It excludes the implementation and variant contracts, implementation and variant Compose assets, runtime settings, image tag, build profile, run ID, and Git provenance. Those values remain in the exact-run manifest, but excluding them from the cohort allows implementations and variants to be compared under the same scenario and measurement conditions.

The build cohort fingerprint includes only the resolved environment,
measurement, and build profile contracts. The implementation and variant,
source workspace, Dockerfile, application artifact declaration, run ID, and Git
provenance stay in the exact build manifest. Consequently, two implementations
using the same three shared profiles receive the same build cohort fingerprint.

## Validation And Execution

The runner validates each manifest against its Draft 2020-12 schema and then
performs strict checkout-bound validation. Contract, workspace, Dockerfile,
artifact, and asset paths must be normalized repository-relative paths, resolve
inside the checkout without symlinks or symlink escapes, exist with the recorded
contents, and reproduce both fingerprints. The selected contracts must also
resolve to the same effective configuration.

Docker Compose execution uses only the validated manifest assets, ordered as environment, implementation, optional variant, and optional scenario overlays. Missing, unsafe, or non-Compose paths are rejected before a measurement command can start.

`metadata.json` and `result.json` repeat the exact `manifest_digest` and `cohort_fingerprint` from `resolved-manifest.json`. This cross-reference binds the normalized and environment metadata to the resolved inputs.

Current local outputs are development evidence, not official benchmark results.
Official results require clean trusted commits, frozen official profiles, and
the complete host and publication validation described in
[automation.md](automation.md).
