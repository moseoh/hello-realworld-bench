from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from hrw_runner.build_config import resolve_build_run_config
from hrw_runner.build_manifest import build_resolved_build_manifest
from hrw_runner.manifest import read_git_provenance


PROJECT_ROOT = Path(__file__).resolve().parents[2]
METRICS = (
    "gradle_clean_build_ms",
    "gradle_incremental_rebuild_ms",
    "image_package_ms",
    "image_rebuild_ms",
)
GRADLE_IMAGE = "eclipse-temurin:25-jdk@sha256:68868d04fa9cfd5f5c6abec0b5cef86d8de2bf9c62c37c7d3e4f0f80f5cfd7ff"
BUILDKIT_IMAGE = "moby/buildkit:buildx-stable-1@sha256:0168606be2315b7c807a03b3d8aa79beefdb31c98740cebdffdfeebf31190c9f"


def _evidence_module():
    try:
        return importlib.import_module("hrw_runner.build_evidence")
    except ModuleNotFoundError:
        raise AssertionError("hrw_runner.build_evidence must exist") from None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def _tree_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _application_descriptor(file_digest: str) -> dict[str, object]:
    files = [
        {
            "path": "build/libs/app.jar",
            "size_bytes": 1,
            "sha256": file_digest,
        }
    ]
    aggregate = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "type": "glob",
        "declaration": "build/libs/*.jar",
        "sha256": aggregate,
        "files": files,
    }


def _fixture(root: Path) -> Path:
    config = resolve_build_run_config(
        "java/spring-boot",
        "jvm-java25",
        PROJECT_ROOT,
        environment_profile="home-build-v1",
        measurement_protocol="official-build-v1",
        build_profile="official-gradle-docker-v1",
    )
    manifest = build_resolved_build_manifest(
        config, "build-run-001", read_git_provenance(PROJECT_ROOT)
    )
    _write(root / "build-resolved-manifest.json", manifest)
    host = {
        "machine_id": "f66cd2d134b94bb18eb7e531d1baf343",
        "cpu_model": "AMD Ryzen 7 5825U",
        "logical_cpu_count": 16,
        "memory_bytes": 32_000_000_000,
        "docker_version": "28.3.2",
        "buildx_version": "github.com/docker/buildx v0.25.0",
        "running_containers": [],
        "builders": ["default"],
        "buildkit_state_volumes": [],
    }
    _write(root / "preflight.json", host)
    _write(root / "postflight.json", host)
    campaign_digest = hashlib.sha256(b"build-run-001").hexdigest()[:16]
    build = manifest["execution"]["build"]
    app_dir = Path(manifest["execution"]["app_dir"])
    context = app_dir if build["context"] == "." else app_dir / build["context"]
    seed_log_path = root / "campaign-operations/01-buildkit-runtime-base-seed.log"
    seed_log_path.parent.mkdir(parents=True, exist_ok=True)
    seed_log_path.write_text("ok\n")
    seed_record = {
        "name": "buildkit_runtime_base_seed",
        "argv": [
            "docker", "buildx", "build",
            "--builder", f"hrw-build-{campaign_digest}-seed",
            "--platform", "linux/amd64",
            "--provenance=false",
            "--file", (app_dir / build["dockerfile"]).as_posix(),
            "--target", "runtime-base",
            "--cache-to", "type=local,dest=/tmp/campaign/buildkit-cache-seed,mode=max",
            "--output", "type=cacheonly",
            context.as_posix(),
        ],
        "working_directory": "repository-root",
        "started_at": "2026-07-14T00:00:00Z",
        "finished_at": "2026-07-14T00:00:01Z",
        "start_monotonic_ns": 1_000_000_000,
        "end_monotonic_ns": 1_010_000_000,
        "duration_ms": 10.0,
        "exit_code": 0,
        "combined_log": {
            "path": seed_log_path.relative_to(root).as_posix(),
            "sha256": _sha(seed_log_path),
            "size_bytes": seed_log_path.stat().st_size,
        },
    }
    seed_record_path = root / "campaign-operations/01-buildkit-runtime-base-seed.json"
    _write(seed_record_path, seed_record)
    cache_seed = {
        "gradle_executor_image": GRADLE_IMAGE,
        "buildkit_image": BUILDKIT_IMAGE,
        "dependency_seed_sha256": "d" * 64,
        "buildkit_cache_seed_sha256": "e" * 64,
        "buildkit_cache_seed_path": "/tmp/campaign/buildkit-cache-seed",
        "seed_builder_name": f"hrw-build-{campaign_digest}-seed",
        "seed_state_volume": (
            f"buildx_buildkit_hrw-build-{campaign_digest}-seed0_state"
        ),
        "buildkit_seed_tree_sha256": _tree_digest(PROJECT_ROOT / context),
        "buildkit_seed_operation": {
            "path": seed_record_path.relative_to(root).as_posix(),
            "sha256": _sha(seed_record_path),
        },
        "dependency_seed_path": "/tmp/dependency-seed",
        "dependency_seed_mode": "supplied",
        "workspace_build_outputs_removed": True,
        "gradle_runtime_state_removed": True,
    }
    _write(root / "cache-seed.json", cache_seed)

    references = []
    trial_documents = []
    for index in range(1, 4):
        trial_id = f"trial-{index:02d}"
        trial_dir = root / "trials" / f"{index:02d}"
        builder_name = f"hrw-build-{campaign_digest}-{index:02d}"
        trial_scratch = Path("/tmp/campaign") / trial_id
        inputs = {
            "trial_evidence_dir": str(trial_dir),
            "workspace": str(trial_scratch / "workspace"),
            "dependency_cache": str(trial_scratch / "dependency-cache"),
            "dependency_seed_sha256": "d" * 64,
            "dependency_cache_initial_sha256": "d" * 64,
            "buildkit_cache_seed": "/tmp/campaign/buildkit-cache-seed",
            "cache_seed_sha256": "e" * 64,
            "builder_name": builder_name,
            "builder_driver": "docker-container",
            "builder_image": BUILDKIT_IMAGE,
            "builder_cpu_quota": 200000,
            "builder_cpu_period": 100000,
            "builder_memory": "4g",
            "builder_memory_swap": "4g",
            "state_volume": f"buildx_buildkit_{builder_name}0_state",
            "image_package_archive": str(trial_dir / "image-package.oci"),
            "image_rebuild_archive": str(trial_dir / "image-rebuild.oci"),
            "image_package_metadata": str(trial_scratch / "image-package-metadata.json"),
            "image_rebuild_metadata": str(trial_scratch / "image-rebuild-metadata.json"),
            "builder_removed": True,
            "state_volume_removed": True,
        }
        operations = []
        metrics = {}
        for operation_index, (name, metric) in enumerate(zip(
            (
                "gradle_clean_build",
                "image_package",
                "gradle_incremental_rebuild",
                "image_rebuild",
            ),
            (
                "gradle_clean_build_ms",
                "image_package_ms",
                "gradle_incremental_rebuild_ms",
                "image_rebuild_ms",
            ),
        ), 1):
            start = index * 100_000_000 + operation_index * 10_000_000
            duration = index * 10 + operation_index
            record = {
                "name": name,
                "argv": _operation_argv(
                    manifest, trial_dir, inputs, name
                ),
                "started_at": "2026-07-14T00:00:00Z",
                "finished_at": "2026-07-14T00:00:01Z",
                "start_monotonic_ns": start,
                "end_monotonic_ns": start + duration * 1_000_000,
                "duration_ms": float(duration),
                "exit_code": 0,
            }
            log_path = trial_dir / "operations" / f"{operation_index:02d}-{name}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("ok\n")
            record["combined_log"] = {
                "path": log_path.relative_to(trial_dir).as_posix(),
                "sha256": _sha(log_path),
                "size_bytes": log_path.stat().st_size,
            }
            path = trial_dir / "operations" / f"{operation_index:02d}-{name}.json"
            _write(path, record)
            operations.append({"name": name, "path": path.relative_to(trial_dir).as_posix(), "sha256": _sha(path)})
            metrics[metric] = float(duration)
        probe = build["incremental_input"]
        probe_path = PROJECT_ROOT / app_dir / probe["path"]
        source_bytes = probe_path.read_bytes()
        mutated_bytes = source_bytes.replace(
            probe["from"].encode(),
            probe["to"].encode(),
            1,
        )
        source = {
            "path": probe["path"],
            "before": {"sha256": hashlib.sha256(source_bytes).hexdigest()},
            "after": {"sha256": hashlib.sha256(mutated_bytes).hexdigest()},
        }
        applications = {
            "before": _application_descriptor("6" * 64),
            "after": _application_descriptor("8" * 64),
        }
        images = {
            "before": _write_oci_descriptor(trial_dir, "image-package", "6", "7", 100),
            "after": _write_oci_descriptor(trial_dir, "image-rebuild", "8", "9", 101),
        }
        evidence = {}
        for name, document in (
            ("source_probe", source),
            ("application_artifacts", applications),
            ("image_artifacts", images),
            ("trial_inputs", inputs),
        ):
            filename = name.replace("_", "-") + ".json"
            path = trial_dir / filename
            _write(path, document)
            evidence[name] = {"path": filename, "sha256": _sha(path)}
        artifact_paths = [operation["path"] for operation in operations]
        artifact_paths += [
            json.loads((trial_dir / operation["path"]).read_text())["combined_log"]["path"]
            for operation in operations
        ]
        artifact_paths += [reference["path"] for reference in evidence.values()]
        for descriptor in images.values():
            artifact_paths.extend(
                [descriptor["index"]["path"], descriptor["manifest"]["path"]]
            )
        artifact_manifest = {
            "schema_version": "1.0",
            "trial_id": trial_id,
            "artifacts": [
                {"path": path, "size_bytes": (trial_dir / path).stat().st_size, "sha256": _sha(trial_dir / path)}
                for path in sorted(artifact_paths)
            ],
        }
        _write(trial_dir / "artifact-manifest.json", artifact_manifest)
        trial = {
            "schema_version": "1.0",
            "run_id": "build-run-001",
            "trial_id": trial_id,
            "manifest_digest": manifest["manifest_digest"],
            "cohort_fingerprint": manifest["cohort"]["fingerprint"],
            "status": "valid",
            "started_at": "2026-07-14T00:00:00Z",
            "finished_at": "2026-07-14T00:00:02Z",
            "metrics": metrics,
            "operations": operations,
            "evidence": evidence,
            "artifact_manifest": {"path": "artifact-manifest.json", "sha256": _sha(trial_dir / "artifact-manifest.json")},
        }
        _write(trial_dir / "build-trial.json", trial)
        trial_documents.append(trial)
        references.append({
            "trial_id": trial_id,
            "index": index,
            "status": "valid",
            "path": f"trials/{index:02d}/build-trial.json",
            "sha256": _sha(trial_dir / "build-trial.json"),
        })

    summaries = {}
    for metric in METRICS:
        values = [{"trial_id": trial["trial_id"], "value": trial["metrics"][metric]} for trial in trial_documents]
        sorted_values = sorted(item["value"] for item in values)
        summaries[metric] = {"min": sorted_values[0], "median": sorted_values[1], "max": sorted_values[2], "trials": values}
    run_set = {
        "schema_version": "1.0",
        "run_set_id": "build-run-001",
        "run_id": "build-run-001",
        "manifest_digest": manifest["manifest_digest"],
        "cohort_fingerprint": manifest["cohort"]["fingerprint"],
        "status": "complete",
        "expected_trials": 3,
        "trials": references,
        "campaign_evidence": {
            "preflight": {"path": "preflight.json", "sha256": _sha(root / "preflight.json")},
            "postflight": {"path": "postflight.json", "sha256": _sha(root / "postflight.json")},
            "cache_seed": {"path": "cache-seed.json", "sha256": _sha(root / "cache-seed.json")},
        },
        "summary": {"trial_count": 3, "valid_trial_count": 3, "build_metrics": summaries},
    }
    _write(root / "build-run-set.json", run_set)
    return root


def _operation_argv(manifest, trial_dir: Path, inputs, name: str):
    build = manifest["execution"]["build"]
    app_dir = Path(inputs["workspace"]) / manifest["execution"]["app_dir"]
    if name in {"gradle_clean_build", "gradle_incremental_rebuild"}:
        command = (
            build["clean_command"]
            if name == "gradle_clean_build"
            else build["incremental_command"]
        )
        return [
            "docker", "run", "--rm",
            "--cpus", "2",
            "--memory", "4g",
            "--memory-swap", "4g",
            "--user", "1000:1000",
            "--network", "none",
            "--mount", f"type=bind,src={app_dir},target=/workspace",
            "--mount", f"type=bind,src={inputs['dependency_cache']},target=/gradle-cache",
            "--env", "GRADLE_USER_HOME=/gradle-cache",
            "--workdir", "/workspace",
            GRADLE_IMAGE,
            *command,
        ]

    operation = "image-package" if name == "image_package" else "image-rebuild"
    metadata_key = (
        "image_package_metadata" if name == "image_package" else "image_rebuild_metadata"
    )
    argv = [
        "docker", "buildx", "build",
        "--builder", inputs["builder_name"],
        "--platform", "linux/amd64",
        "--provenance=false",
        "--file", str(app_dir / build["dockerfile"]),
    ]
    if name == "image_package":
        argv.extend(
            ["--cache-from", f"type=local,src={inputs['buildkit_cache_seed']}"]
        )
    argv.extend(
        [
            "--output", f"type=oci,dest={inputs[operation.replace('-', '_') + '_archive']}",
            "--metadata-file", inputs[metadata_key],
            str(app_dir / build["context"]),
        ]
    )
    return argv


def _rehash_operation(run_set_dir: Path, operation_index: int, mutate):
    trial_dir = run_set_dir / "trials/01"
    trial_path = trial_dir / "build-trial.json"
    trial = json.loads(trial_path.read_text())
    operation_ref = trial["operations"][operation_index]
    operation_path = trial_dir / operation_ref["path"]
    operation = json.loads(operation_path.read_text())
    mutate(operation["argv"])
    _write(operation_path, operation)
    operation_ref["sha256"] = _sha(operation_path)

    artifact_path = trial_dir / "artifact-manifest.json"
    artifact_manifest = json.loads(artifact_path.read_text())
    artifact = next(
        item for item in artifact_manifest["artifacts"]
        if item["path"] == operation_ref["path"]
    )
    artifact["size_bytes"] = operation_path.stat().st_size
    artifact["sha256"] = _sha(operation_path)
    _write(artifact_path, artifact_manifest)
    trial["artifact_manifest"]["sha256"] = _sha(artifact_path)
    _write(trial_path, trial)

    run_set_path = run_set_dir / "build-run-set.json"
    run_set = json.loads(run_set_path.read_text())
    run_set["trials"][0]["sha256"] = _sha(trial_path)
    _write(run_set_path, run_set)


def _rehash_trial_inputs(run_set_dir: Path, mutate):
    trial_dir = run_set_dir / "trials/01"
    inputs_path = trial_dir / "trial-inputs.json"
    inputs = json.loads(inputs_path.read_text())
    mutate(inputs)
    _write(inputs_path, inputs)

    trial_path = trial_dir / "build-trial.json"
    trial = json.loads(trial_path.read_text())
    trial["evidence"]["trial_inputs"]["sha256"] = _sha(inputs_path)
    artifact_path = trial_dir / "artifact-manifest.json"
    artifact_manifest = json.loads(artifact_path.read_text())
    artifact = next(
        item for item in artifact_manifest["artifacts"]
        if item["path"] == "trial-inputs.json"
    )
    artifact["size_bytes"] = inputs_path.stat().st_size
    artifact["sha256"] = _sha(inputs_path)
    _write(artifact_path, artifact_manifest)
    trial["artifact_manifest"]["sha256"] = _sha(artifact_path)
    _write(trial_path, trial)

    run_set_path = run_set_dir / "build-run-set.json"
    run_set = json.loads(run_set_path.read_text())
    run_set["trials"][0]["sha256"] = _sha(trial_path)
    _write(run_set_path, run_set)


def _rehash_trial_evidence(run_set_dir: Path, filename: str, evidence_name: str, mutate):
    trial_dir = run_set_dir / "trials/01"
    evidence_path = trial_dir / filename
    value = json.loads(evidence_path.read_text())
    mutate(value)
    _write(evidence_path, value)

    trial_path = trial_dir / "build-trial.json"
    trial = json.loads(trial_path.read_text())
    trial["evidence"][evidence_name]["sha256"] = _sha(evidence_path)
    artifact_path = trial_dir / "artifact-manifest.json"
    artifact_manifest = json.loads(artifact_path.read_text())
    artifact = next(
        item for item in artifact_manifest["artifacts"]
        if item["path"] == filename
    )
    artifact["size_bytes"] = evidence_path.stat().st_size
    artifact["sha256"] = _sha(evidence_path)
    _write(artifact_path, artifact_manifest)
    trial["artifact_manifest"]["sha256"] = _sha(artifact_path)
    _write(trial_path, trial)

    run_set_path = run_set_dir / "build-run-set.json"
    run_set = json.loads(run_set_path.read_text())
    run_set["trials"][0]["sha256"] = _sha(trial_path)
    _write(run_set_path, run_set)


def _rehash_cache_seed(run_set_dir: Path, mutate_record=None, mutate_seed=None):
    seed_path = run_set_dir / "cache-seed.json"
    seed = json.loads(seed_path.read_text())
    if mutate_record is not None:
        record_path = run_set_dir / seed["buildkit_seed_operation"]["path"]
        record = json.loads(record_path.read_text())
        mutate_record(record)
        _write(record_path, record)
        seed["buildkit_seed_operation"]["sha256"] = _sha(record_path)
    if mutate_seed is not None:
        mutate_seed(seed)
    _write(seed_path, seed)
    run_set_path = run_set_dir / "build-run-set.json"
    run_set = json.loads(run_set_path.read_text())
    run_set["campaign_evidence"]["cache_seed"]["sha256"] = _sha(seed_path)
    _write(run_set_path, run_set)


def _write_oci_descriptor(
    trial_dir: Path,
    operation: str,
    config_character: str,
    layer_character: str,
    archive_size: int,
    *,
    manifest_override=None,
):
    manifest = manifest_override or {
        "schemaVersion": 2,
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:" + config_character * 64,
            "size": 123,
        },
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": "sha256:" + layer_character * 64,
                "size": 456,
            }
        ],
    }
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": "sha256:" + digest,
                "size": len(manifest_bytes),
            }
        ],
    }
    metadata_dir = trial_dir / "oci" / operation
    metadata_dir.mkdir(parents=True, exist_ok=True)
    index_path = metadata_dir / "index.json"
    manifest_path = metadata_dir / "manifest.json"
    index_path.write_bytes(json.dumps(index, separators=(",", ":")).encode())
    manifest_path.write_bytes(manifest_bytes)

    def reference(path: Path):
        return {
            "path": path.relative_to(trial_dir).as_posix(),
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
        }

    return {
        "image_digest": "sha256:" + digest,
        "archive_sha256": hashlib.sha256(("archive-" + digest).encode()).hexdigest(),
        "archive_size_bytes": archive_size,
        "index": reference(index_path),
        "manifest": reference(manifest_path),
    }


class BuildEvidenceValidationTest(unittest.TestCase):
    def test_build_run_set_schema_rejects_unsafe_full_string_ids(self):
        module = _evidence_module()
        unsafe_ids = (
            "valid\nBASH_ENV=../raw-run-set/payload.sh",
            "valid\x01control",
            "valid\n",
            "-leading-separator",
        )
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            run_set = json.loads((run_set_dir / "build-run-set.json").read_text())
            for unsafe_id in unsafe_ids:
                with self.subTest(unsafe_id=repr(unsafe_id)):
                    changed = copy.deepcopy(run_set)
                    changed["run_set_id"] = unsafe_id
                    with self.assertRaisesRegex(ValueError, "run_set_id"):
                        module.validate_build_document(
                            changed, "build-run-set", PROJECT_ROOT
                        )

    def test_build_publication_binds_run_set_id_to_run_id(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            run_set_path = run_set_dir / "build-run-set.json"
            run_set = json.loads(run_set_path.read_text())
            run_set["run_set_id"] = "different-safe-id"
            _write(run_set_path, run_set)

            with self.assertRaisesRegex(ValueError, "run_set_id.*run_id"):
                module.validate_build_publication_evidence(
                    run_set_dir, PROJECT_ROOT
                )

    def test_accepts_three_valid_trials_and_recomputed_summaries(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_rejects_cache_seed_command_and_tree_tampering_after_rehash(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            _rehash_cache_seed(
                run_set_dir,
                mutate_record=lambda record: record["argv"].__setitem__(
                    record["argv"].index("--target") + 1,
                    "full-application",
                ),
            )

            with self.assertRaisesRegex(ValueError, "seed argv"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            _rehash_cache_seed(
                run_set_dir,
                mutate_seed=lambda seed: seed.__setitem__(
                    "buildkit_seed_tree_sha256",
                    "0" * 64,
                ),
            )

            with self.assertRaisesRegex(ValueError, "seed tree"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_rejects_probe_contract_tampering_after_rehash(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            _rehash_trial_evidence(
                run_set_dir,
                "source-probe.json",
                "source_probe",
                lambda source: source.__setitem__("path", "src/main/java/Other.java"),
            )

            with self.assertRaisesRegex(ValueError, "source probe path"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            _rehash_trial_evidence(
                run_set_dir,
                "source-probe.json",
                "source_probe",
                lambda source: source["after"].__setitem__("sha256", "0" * 64),
            )

            with self.assertRaisesRegex(ValueError, "source probe after digest"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_rejects_application_declaration_and_aggregate_tampering_after_rehash(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            _rehash_trial_evidence(
                run_set_dir,
                "application-artifacts.json",
                "application_artifacts",
                lambda artifacts: artifacts["before"].__setitem__(
                    "declaration",
                    "build/libs/other-*.jar",
                ),
            )

            with self.assertRaisesRegex(ValueError, "application artifact declaration"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            _rehash_trial_evidence(
                run_set_dir,
                "application-artifacts.json",
                "application_artifacts",
                lambda artifacts: artifacts["after"].__setitem__("sha256", "0" * 64),
            )

            with self.assertRaisesRegex(ValueError, "application artifact aggregate"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_calls_family_specific_resolved_manifest_validation(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            manifest_path = run_set_dir / "build-resolved-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["execution"]["build"]["context"] = "tampered"
            _write(manifest_path, manifest)
            with self.assertRaises(ValueError):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_accepts_evidence_relocated_for_publication_validation(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = _fixture(root / "original")
            relocated = root / "relocated"
            shutil.copytree(original, relocated)

            module.validate_build_publication_evidence(relocated, PROJECT_ROOT)

    def test_binds_hosted_validation_to_expected_implementation_and_variant(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            module.validate_build_publication_evidence(
                run_set_dir,
                PROJECT_ROOT,
                expected_implementation="java/spring-boot",
                expected_variant="jvm-java25",
            )

            with self.assertRaisesRegex(ValueError, "expected implementation"):
                module.validate_build_publication_evidence(
                    run_set_dir,
                    PROJECT_ROOT,
                    expected_implementation="java/quarkus",
                    expected_variant="jvm-java25",
                )

    def test_rejects_extra_regular_files_and_symlinks_in_raw_run_set(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            (run_set_dir / "unvalidated.txt").write_text("extra\n")

            with self.assertRaisesRegex(ValueError, "closed file set"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            (run_set_dir / "unvalidated-link").symlink_to(
                run_set_dir / "preflight.json"
            )

            with self.assertRaisesRegex(ValueError, "symlink"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_deterministic_raw_archive_is_stable_across_retry_and_mtime_changes(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_set_dir = _fixture(root / "raw")
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            module.create_deterministic_build_archive(
                run_set_dir,
                PROJECT_ROOT,
                first,
                expected_implementation="java/spring-boot",
                expected_variant="jvm-java25",
            )
            for path in run_set_dir.rglob("*"):
                if path.is_file():
                    os.utime(path, (2_000_000_000, 2_000_000_000))
            module.create_deterministic_build_archive(
                run_set_dir,
                PROJECT_ROOT,
                second,
                expected_implementation="java/spring-boot",
                expected_variant="jvm-java25",
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_rejects_operation_argv_contract_tampering_after_rehash(self):
        module = _evidence_module()

        def replace_value(flag, value):
            return lambda argv: argv.__setitem__(argv.index(flag) + 1, value)

        def replace_literal(old, new):
            return lambda argv: argv.__setitem__(argv.index(old), new)

        def remove_option(flag):
            def mutate(argv):
                index = argv.index(flag)
                del argv[index:index + 2]
            return mutate

        def add_rebuild_cache(argv):
            index = argv.index("--output")
            argv[index:index] = [
                "--cache-from", "type=local,src=/tmp/buildkit-cache-seed"
            ]

        cases = (
            (0, lambda argv: argv.__setitem__(slice(None), ["example", "gradle_clean_build"])),
            (0, replace_literal(GRADLE_IMAGE, "eclipse-temurin:25-jdk")),
            (0, replace_value("--cpus", "3")),
            (0, replace_value("--memory", "5g")),
            (0, replace_value("--memory-swap", "5g")),
            (0, replace_value("--mount", "type=bind,src=/tmp/wrong,target=/workspace")),
            (0, replace_value("--env", "GRADLE_USER_HOME=/tmp/wrong")),
            (0, replace_literal("--offline", "--refresh-dependencies")),
            (0, replace_literal("--no-daemon", "--daemon")),
            (0, replace_literal("--no-build-cache", "--build-cache")),
            (0, replace_literal("clean", "assemble")),
            (1, replace_value("--builder", "hrw-build-wrong-01")),
            (1, replace_value("--platform", "linux/arm64")),
            (1, replace_literal("--provenance=false", "--provenance=true")),
            (1, replace_value("--output", "type=docker")),
            (1, replace_value("--metadata-file", "/tmp/wrong-metadata.json")),
            (1, remove_option("--cache-from")),
            (3, add_rebuild_cache),
        )
        for operation_index, mutate in cases:
            with self.subTest(operation_index=operation_index, mutate=mutate):
                with tempfile.TemporaryDirectory() as directory:
                    run_set_dir = _fixture(Path(directory))
                    _rehash_operation(run_set_dir, operation_index, mutate)
                    with self.assertRaisesRegex(ValueError, "argv"):
                        module.validate_build_publication_evidence(
                            run_set_dir, PROJECT_ROOT
                        )

    def test_rejects_buildkit_builder_context_tampering_after_rehash(self):
        module = _evidence_module()
        cases = (
            ("builder_image", "moby/buildkit:latest"),
            ("builder_cpu_quota", 300000),
            ("builder_memory", "5g"),
            ("builder_memory_swap", "5g"),
            ("state_volume", "buildx_buildkit_other0_state"),
            ("buildkit_cache_seed", "/tmp/other-cache"),
        )
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                run_set_dir = _fixture(Path(directory))
                _rehash_trial_inputs(
                    run_set_dir,
                    lambda inputs, field=field, value=value: inputs.__setitem__(
                        field, value
                    ),
                )
                with self.assertRaisesRegex(ValueError, "argv"):
                    module.validate_build_publication_evidence(
                        run_set_dir, PROJECT_ROOT
                    )

    def test_rejects_inconsistent_oci_index_manifest_bytes_and_descriptors(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            trial_dir = Path(directory)
            descriptor = _write_oci_descriptor(trial_dir, "package", "6", "7", 100)
            index_path = trial_dir / descriptor["index"]["path"]
            index = json.loads(index_path.read_text())
            index["manifests"][0]["digest"] = "sha256:" + "0" * 64
            index_path.write_text(json.dumps(index))
            descriptor["index"]["sha256"] = _sha(index_path)
            descriptor["index"]["size_bytes"] = index_path.stat().st_size
            with self.assertRaises(ValueError):
                module.validate_oci_descriptor(descriptor, trial_dir)

        with tempfile.TemporaryDirectory() as directory:
            trial_dir = Path(directory)
            invalid_manifest = {"schemaVersion": 2, "config": {}, "layers": []}
            descriptor = _write_oci_descriptor(
                trial_dir,
                "package",
                "6",
                "7",
                100,
                manifest_override=invalid_manifest,
            )
            with self.assertRaises(ValueError):
                module.validate_oci_descriptor(descriptor, trial_dir)

    def test_rejects_duration_operation_order_and_digest_tampering(self):
        module = _evidence_module()
        mutations = (
            lambda trial: trial["metrics"].__setitem__("gradle_clean_build_ms", 999),
            lambda trial: trial["operations"].reverse(),
            lambda trial: trial["evidence"]["source_probe"].__setitem__("sha256", "0" * 64),
            lambda trial: trial.__setitem__("status", "failed"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                run_set_dir = _fixture(Path(directory))
                trial_path = run_set_dir / "trials/01/build-trial.json"
                trial = json.loads(trial_path.read_text())
                mutate(trial)
                _write(trial_path, trial)
                run_set = json.loads((run_set_dir / "build-run-set.json").read_text())
                run_set["trials"][0]["sha256"] = _sha(trial_path)
                _write(run_set_dir / "build-run-set.json", run_set)
                with self.assertRaises(ValueError):
                    module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_rejects_combined_log_and_campaign_seed_tampering(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            log_path = run_set_dir / "trials/01/operations/01-gradle_clean_build.log"
            log_path.write_text("tampered\n")
            with self.assertRaisesRegex(ValueError, "log"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            seed_path = run_set_dir / "cache-seed.json"
            seed = json.loads(seed_path.read_text())
            seed["dependency_seed_sha256"] = "0" * 64
            _write(seed_path, seed)
            run_set = json.loads((run_set_dir / "build-run-set.json").read_text())
            run_set["campaign_evidence"]["cache_seed"]["sha256"] = _sha(seed_path)
            _write(run_set_dir / "build-run-set.json", run_set)
            with self.assertRaisesRegex(ValueError, "seed"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_rejects_postflight_container_builder_and_state_volume_residue(self):
        module = _evidence_module()
        for field, value in (
            ("running_containers", ["container-added"]),
            ("builders", ["default", "hrw-build-trial-01"]),
            ("buildkit_state_volumes", ["buildx_buildkit_hrw-build-trial-01_state"]),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                run_set_dir = _fixture(Path(directory))
                postflight_path = run_set_dir / "postflight.json"
                postflight = json.loads(postflight_path.read_text())
                postflight[field] = value
                _write(postflight_path, postflight)
                run_set = json.loads((run_set_dir / "build-run-set.json").read_text())
                run_set["campaign_evidence"]["postflight"]["sha256"] = _sha(postflight_path)
                _write(run_set_dir / "build-run-set.json", run_set)
                with self.assertRaises(ValueError):
                    module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_accepts_the_same_running_container_set_in_a_different_order(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            preflight_path = run_set_dir / "preflight.json"
            postflight_path = run_set_dir / "postflight.json"
            preflight = json.loads(preflight_path.read_text())
            postflight = json.loads(postflight_path.read_text())
            preflight["running_containers"] = ["one", "two"]
            postflight["running_containers"] = ["two", "one"]
            _write(preflight_path, preflight)
            _write(postflight_path, postflight)
            run_set_path = run_set_dir / "build-run-set.json"
            run_set = json.loads(run_set_path.read_text())
            run_set["campaign_evidence"]["preflight"]["sha256"] = _sha(preflight_path)
            run_set["campaign_evidence"]["postflight"]["sha256"] = _sha(postflight_path)
            _write(run_set_path, run_set)

            module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_rejects_non_unique_trial_paths_and_summary_linkage_tampering(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            run_set_path = run_set_dir / "build-run-set.json"
            run_set = json.loads(run_set_path.read_text())
            run_set["trials"][1]["path"] = run_set["trials"][0]["path"]
            _write(run_set_path, run_set)
            with self.assertRaises(ValueError):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            run_set_path = run_set_dir / "build-run-set.json"
            run_set = json.loads(run_set_path.read_text())
            run_set["summary"]["build_metrics"]["image_rebuild_ms"]["median"] += 1
            _write(run_set_path, run_set)
            with self.assertRaisesRegex(ValueError, "summary"):
                module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_rejects_unchanged_probe_application_and_image_digests(self):
        module = _evidence_module()
        for filename, key, field in (
            ("source-probe.json", "after", "sha256"),
            ("application-artifacts.json", "after", "sha256"),
            ("image-artifacts.json", "after", "image_digest"),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                run_set_dir = _fixture(Path(directory))
                evidence_name = filename.removesuffix(".json").replace("-", "_")
                _rehash_trial_evidence(
                    run_set_dir,
                    filename,
                    evidence_name,
                    lambda value, key=key, field=field: value[key].__setitem__(
                        field,
                        value["before"][field],
                    ),
                )
                with self.assertRaisesRegex(ValueError, "digest did not change"):
                    module.validate_build_publication_evidence(run_set_dir, PROJECT_ROOT)

    def test_build_schemas_reject_non_build_fields(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
            trial = json.loads((run_set_dir / "trials/01/build-trial.json").read_text())
            trial["startup"] = {}
            with self.assertRaises(ValueError):
                module.validate_build_document(trial, "build-trial", PROJECT_ROOT)
            run_set = json.loads((run_set_dir / "build-run-set.json").read_text())
            run_set["scenario"] = "ping-api"
            with self.assertRaises(ValueError):
                module.validate_build_document(run_set, "build-run-set", PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
