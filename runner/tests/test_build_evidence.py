from __future__ import annotations

import copy
import hashlib
import importlib
import json
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
    cache_seed = {
        "gradle_executor_image": "eclipse-temurin:25-jdk@sha256:68868d04fa9cfd5f5c6abec0b5cef86d8de2bf9c62c37c7d3e4f0f80f5cfd7ff",
        "buildkit_image": "moby/buildkit:buildx-stable-1@sha256:0168606be2315b7c807a03b3d8aa79beefdb31c98740cebdffdfeebf31190c9f",
        "dependency_seed_sha256": "d" * 64,
        "buildkit_cache_seed_sha256": "e" * 64,
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
                "argv": ["example", name],
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
        source = {
            "before": {"sha256": "3" * 64},
            "after": {"sha256": "4" * 64},
        }
        applications = {
            "before": {"sha256": "5" * 64, "files": [{"path": "build/libs/app.jar", "size_bytes": 1, "sha256": "6" * 64}]},
            "after": {"sha256": "7" * 64, "files": [{"path": "build/libs/app.jar", "size_bytes": 1, "sha256": "8" * 64}]},
        }
        images = {
            "before": _write_oci_descriptor(trial_dir, "image-package", "6", "7", 100),
            "after": _write_oci_descriptor(trial_dir, "image-rebuild", "8", "9", 101),
        }
        inputs = {
            "workspace": f"/tmp/workspace-{index}",
            "dependency_cache": f"/tmp/cache-{index}",
            "dependency_seed_sha256": "d" * 64,
            "dependency_cache_initial_sha256": "d" * 64,
            "cache_seed_sha256": "e" * 64,
            "builder_name": f"hrw-build-{index}",
            "builder_removed": True,
            "state_volume_removed": True,
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
    def test_accepts_three_valid_trials_and_recomputed_summaries(self):
        module = _evidence_module()
        with tempfile.TemporaryDirectory() as directory:
            run_set_dir = _fixture(Path(directory))
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
                trial_dir = run_set_dir / "trials/01"
                path = trial_dir / filename
                value = json.loads(path.read_text())
                before_field = field
                value[key][field] = value["before"][before_field]
                _write(path, value)
                with self.assertRaises(ValueError):
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
