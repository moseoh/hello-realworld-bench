import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hrw_runner.config import resolve_run_config
from hrw_runner.manifest import build_resolved_manifest
from hrw_runner.publication import (
    PublicationError,
    _validate_catalog_entries,
    _validate_promotion,
    publish_run_set,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class DatasetPublicationTest(unittest.TestCase):
    def test_non_build_catalog_requires_valid_matching_image_digests(self):
        for family in ("service", "lifecycle", None):
            for digest in (None, "sha256:not-a-digest"):
                with (
                    self.subTest(family=family, digest=digest),
                    tempfile.TemporaryDirectory() as temp_dir,
                ):
                    root = Path(temp_dir)
                    run_set_dir = self._write_run_set(root / "source", "run-001")
                    dataset_dir = root / "dataset"
                    with (
                        patch("hrw_runner.publication.validate_run_set_evidence"),
                        patch("hrw_runner.publication.validate_resolved_manifest"),
                    ):
                        entry_dir = publish_run_set(
                            run_set_dir,
                            dataset_dir,
                            PROJECT_ROOT,
                            source_commit="c" * 40,
                        )

                    catalog_path = dataset_dir / "catalog.json"
                    catalog = json.loads(catalog_path.read_text())
                    entry = catalog["entries"][0]
                    if family is None:
                        entry.pop("evidence_family")
                    else:
                        entry["evidence_family"] = family
                    publication_path = entry_dir / "publication.json"
                    publication = json.loads(publication_path.read_text())
                    if digest is None:
                        entry.pop("image_digest")
                        publication.pop("image_digest")
                    else:
                        entry["image_digest"] = digest
                        publication["image_digest"] = digest
                    publication_path.write_text(json.dumps(publication))
                    entry["publication_sha256"] = hashlib.sha256(
                        publication_path.read_bytes()
                    ).hexdigest()

                    with self.assertRaisesRegex(PublicationError, "image"):
                        _validate_catalog_entries(dataset_dir, catalog)

    def test_lifecycle_publication_revalidates_raw_lifecycle_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_run_set(root / "source", "run-001")
            manifest_path = run_set_dir / "resolved-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["selection"].update(
                scenario="cold-start-api",
                load_profile="none",
                environment_profile="home-k3s-lifecycle-v1",
                measurement_protocol="official-cold-start-v1",
            )
            manifest["cohort"]["evidence_family"] = "lifecycle"
            manifest_path.write_text(json.dumps(manifest))

            with (
                patch("hrw_runner.publication.validate_run_set_evidence"),
                patch("hrw_runner.publication.validate_resolved_manifest"),
                patch(
                    "hrw_runner.publication.validate_lifecycle_publication_evidence",
                    side_effect=ValueError("invalid lifecycle evidence"),
                ),
                self.assertRaisesRegex(ValueError, "invalid lifecycle evidence"),
            ):
                publish_run_set(
                    run_set_dir,
                    root / "dataset",
                    PROJECT_ROOT,
                    source_commit="c" * 40,
                )

    def test_allows_only_the_frozen_official_lifecycle_combination(self):
        run_set = {
            "status": "complete",
            "expected_trials": 1,
            "trials": [{"status": "valid"}],
            "summary": {"valid_trial_count": 1},
        }
        manifest = {
            "source": {"git_commit": "c" * 40, "git_dirty": False},
            "selection": {
                "scenario": "cold-start-api",
                "load_profile": "none",
                "environment_profile": "home-k3s-lifecycle-v1",
                "measurement_protocol": "official-cold-start-v1",
            },
            "cohort": {"evidence_family": "lifecycle"},
        }

        _validate_promotion(run_set, manifest, "c" * 40)

        for field, value in (
            ("scenario", "ping-api"),
            ("load_profile", "steady"),
            ("environment_profile", "home-k3s-v1"),
            ("measurement_protocol", "cold-start"),
        ):
            changed = json.loads(json.dumps(manifest))
            changed["selection"][field] = value
            with self.subTest(field=field), self.assertRaises(PublicationError):
                _validate_promotion(run_set, changed, "c" * 40)

    def test_allows_only_the_frozen_official_build_combination(self):
        run_set = {
            "status": "complete",
            "expected_trials": 3,
            "trials": [{"status": "valid"}] * 3,
            "summary": {"valid_trial_count": 3},
        }
        manifest = {
            "source": {"git_commit": "c" * 40, "git_dirty": False},
            "selection": {
                "environment_profile": "home-build-v1",
                "measurement_protocol": "official-build-v1",
                "build_profile": "official-gradle-docker-v1",
            },
            "cohort": {"evidence_family": "build"},
        }

        _validate_promotion(run_set, manifest, "c" * 40)

        for field, value in (
            ("environment_profile", "home-k3s-v1"),
            ("measurement_protocol", "official-service-v1"),
            ("build_profile", "local-gradle-docker"),
        ):
            changed = json.loads(json.dumps(manifest))
            changed["selection"][field] = value
            with self.subTest(field=field), self.assertRaises(PublicationError):
                _validate_promotion(run_set, changed, "c" * 40)

    def test_publishes_compact_build_evidence_without_operation_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_build_run_set(root / "source", "build-001")
            dataset_dir = root / "dataset"

            with patch(
                "hrw_runner.publication.validate_build_publication_evidence"
            ) as validate:
                entry_dir = publish_run_set(
                    run_set_dir,
                    dataset_dir,
                    PROJECT_ROOT,
                    source_commit="c" * 40,
                )

            validate.assert_called_once_with(run_set_dir.resolve(), PROJECT_ROOT)
            self.assertEqual(
                entry_dir,
                dataset_dir / "build-run-sets" / DIGEST_B / "build-001",
            )
            self.assertTrue((entry_dir / "build-resolved-manifest.json").is_file())
            self.assertTrue((entry_dir / "build-run-set.json").is_file())
            self.assertTrue((entry_dir / "preflight.json").is_file())
            self.assertTrue((entry_dir / "postflight.json").is_file())
            self.assertTrue((entry_dir / "cache-seed.json").is_file())
            self.assertTrue((entry_dir / "trials/01/build-trial.json").is_file())
            self.assertTrue(
                (entry_dir / "trials/01/artifact-manifest.json").is_file()
            )
            self.assertFalse((entry_dir / "trials/01/operation.log").exists())

            catalog = json.loads((dataset_dir / "catalog.json").read_text())
            entry = catalog["entries"][0]
            self.assertEqual(entry["evidence_family"], "build")
            self.assertEqual(entry["path"], "build-run-sets/" + DIGEST_B + "/build-001")
            self.assertNotIn("image_digest", entry)
            self.assertNotIn("scenario", entry["selection"])
            self.assertNotIn("load_profile", entry["selection"])

    def test_build_publication_is_idempotent_and_preserves_legacy_catalog_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset_dir = root / "dataset"
            legacy = self._write_run_set(root / "legacy", "service-001")
            build = self._write_build_run_set(root / "build", "build-001")

            with (
                patch("hrw_runner.publication.validate_run_set_evidence"),
                patch("hrw_runner.publication.validate_resolved_manifest"),
                patch("hrw_runner.publication.validate_build_publication_evidence"),
            ):
                publish_run_set(
                    legacy, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )
                catalog_path = dataset_dir / "catalog.json"
                catalog = json.loads(catalog_path.read_text())
                catalog["entries"][0].pop("evidence_family")
                legacy_entry_dir = dataset_dir / catalog["entries"][0]["path"]
                publication_path = legacy_entry_dir / "publication.json"
                publication = json.loads(publication_path.read_text())
                for field in ("evidence_family", "selection", "started_at", "finished_at"):
                    publication.pop(field)
                publication_path.write_text(json.dumps(publication))
                catalog["entries"][0]["publication_sha256"] = hashlib.sha256(
                    publication_path.read_bytes()
                ).hexdigest()
                catalog_path.write_text(json.dumps(catalog))
                publish_run_set(
                    legacy, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )
                first = publish_run_set(
                    build, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )
                second = publish_run_set(
                    build, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )

            self.assertEqual(first, second)
            catalog = json.loads((dataset_dir / "catalog.json").read_text())
            legacy_entry, build_entry = catalog["entries"]
            self.assertEqual(
                legacy_entry["path"], "run-sets/" + DIGEST_B + "/service-001"
            )
            self.assertNotIn("evidence_family", legacy_entry)
            self.assertEqual(build_entry["path"], "build-run-sets/" + DIGEST_B + "/build-001")

    def test_catalog_identity_is_bound_to_hashed_compact_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_build_run_set(root / "source", "build-001")
            dataset_dir = root / "dataset"

            with patch("hrw_runner.publication.validate_build_publication_evidence"):
                entry_dir = publish_run_set(
                    run_set_dir,
                    dataset_dir,
                    PROJECT_ROOT,
                    source_commit="c" * 40,
                )

            publication_path = entry_dir / "publication.json"
            publication = json.loads(publication_path.read_text())
            publication["selection"]["implementation"] = "java/quarkus"
            publication_path.write_text(json.dumps(publication))
            catalog_path = dataset_dir / "catalog.json"
            catalog = json.loads(catalog_path.read_text())
            catalog["entries"][0]["selection"]["implementation"] = "java/quarkus"
            catalog["entries"][0]["publication_sha256"] = hashlib.sha256(
                publication_path.read_bytes()
            ).hexdigest()

            with self.assertRaisesRegex(PublicationError, "compact evidence identity"):
                _validate_catalog_entries(dataset_dir, catalog)

    def test_family_omission_is_rejected_for_modern_publication_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_run_set(root / "source", "service-001")
            dataset_dir = root / "dataset"

            with (
                patch("hrw_runner.publication.validate_run_set_evidence"),
                patch("hrw_runner.publication.validate_resolved_manifest"),
            ):
                publish_run_set(
                    run_set_dir,
                    dataset_dir,
                    PROJECT_ROOT,
                    source_commit="c" * 40,
                )
            catalog = json.loads((dataset_dir / "catalog.json").read_text())
            catalog["entries"][0].pop("evidence_family")

            with self.assertRaisesRegex(PublicationError, "legacy"):
                _validate_catalog_entries(dataset_dir, catalog)

    def test_rejects_tampered_build_catalog_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_build_run_set(root / "source", "build-001")
            dataset_dir = root / "dataset"

            with patch("hrw_runner.publication.validate_build_publication_evidence"):
                entry_dir = publish_run_set(
                    run_set_dir, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )
                (entry_dir / "build-run-set.json").write_text("tampered\n")
                with self.assertRaisesRegex(PublicationError, "append-only"):
                    publish_run_set(
                        run_set_dir,
                        dataset_dir,
                        PROJECT_ROOT,
                        source_commit="c" * 40,
                    )

    def test_publishes_only_compact_evidence_and_updates_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_run_set(root / "source", "run-001")
            dataset_dir = root / "dataset"

            with (
                patch("hrw_runner.publication.validate_run_set_evidence") as validate,
                patch(
                    "hrw_runner.publication.validate_resolved_manifest",
                ) as validate_manifest,
            ):
                entry_dir = publish_run_set(
                    run_set_dir,
                    dataset_dir,
                    PROJECT_ROOT,
                    source_commit="c" * 40,
                    workflow_url="https://github.com/example/actions/runs/1",
                    raw_artifact_url="https://github.com/example/actions/runs/1/artifacts/2",
                    raw_artifact_sha256="1" * 64,
                )

            validate.assert_called_once_with(run_set_dir.resolve(), PROJECT_ROOT)
            validate_manifest.assert_called_once()
            self.assertEqual(validate_manifest.call_args.args[1], PROJECT_ROOT)
            self.assertTrue((entry_dir / "run-set.json").is_file())
            self.assertTrue((entry_dir / "trials/01/result.json").is_file())
            self.assertTrue((entry_dir / "trials/01/time-series.json").is_file())
            self.assertFalse((entry_dir / "trials/01/k6.log").exists())
            catalog = json.loads((dataset_dir / "catalog.json").read_text())
            self.assertEqual(catalog["schema_version"], "1.0")
            self.assertEqual(catalog["entries"][0]["run_set_id"], "run-001")
            self.assertEqual(catalog["entries"][0]["source_commit"], "c" * 40)

    def test_republishing_identical_entry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_run_set(root / "source", "run-001")
            dataset_dir = root / "dataset"

            with (
                patch("hrw_runner.publication.validate_run_set_evidence"),
                patch(
                    "hrw_runner.publication.validate_resolved_manifest",
                ),
            ):
                first = publish_run_set(
                    run_set_dir, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )
                second = publish_run_set(
                    run_set_dir, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )

            self.assertEqual(first, second)
            catalog = json.loads((dataset_dir / "catalog.json").read_text())
            self.assertEqual(len(catalog["entries"]), 1)

    def test_rejects_conflicting_existing_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_run_set(root / "source", "run-001")
            dataset_dir = root / "dataset"

            with (
                patch("hrw_runner.publication.validate_run_set_evidence"),
                patch(
                    "hrw_runner.publication.validate_resolved_manifest",
                ),
            ):
                entry_dir = publish_run_set(
                    run_set_dir, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )
                (entry_dir / "run-set.json").write_text("tampered\n")
                with self.assertRaisesRegex(PublicationError, "append-only"):
                    publish_run_set(
                        run_set_dir, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                    )

    def test_rejects_tampering_in_an_unrelated_catalog_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = self._write_run_set(root / "first", "run-001")
            second = self._write_run_set(root / "second", "run-002")
            dataset_dir = root / "dataset"

            with (
                patch("hrw_runner.publication.validate_run_set_evidence"),
                patch(
                    "hrw_runner.publication.validate_resolved_manifest",
                ),
            ):
                first_entry = publish_run_set(
                    first, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                )
                (first_entry / "trials/01/result.json").write_text("tampered\n")
                with self.assertRaisesRegex(PublicationError, "content has changed"):
                    publish_run_set(
                        second, dataset_dir, PROJECT_ROOT, source_commit="c" * 40
                    )

    def test_rejects_incomplete_invalid_dirty_or_untrusted_run_sets(self):
        mutations = {
            "incomplete": lambda run_set, manifest: run_set.update(status="failed"),
            "invalid trial": lambda run_set, manifest: run_set["trials"][0].update(
                status="invalid"
            ),
            "dirty source": lambda run_set, manifest: manifest["source"].update(
                git_dirty=True
            ),
            "wrong commit": lambda run_set, manifest: None,
            "local environment": lambda run_set, manifest: manifest["selection"].update(
                environment_profile="local-docker-compose"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                run_set_dir = self._write_run_set(root / "source", "run-001")
                run_set = json.loads((run_set_dir / "run-set.json").read_text())
                manifest = json.loads((run_set_dir / "resolved-manifest.json").read_text())
                mutate(run_set, manifest)
                (run_set_dir / "run-set.json").write_text(json.dumps(run_set))
                (run_set_dir / "resolved-manifest.json").write_text(json.dumps(manifest))
                source_commit = "d" * 40 if name == "wrong commit" else "c" * 40

                with (
                    patch("hrw_runner.publication.validate_run_set_evidence"),
                    patch(
                        "hrw_runner.publication.validate_resolved_manifest",
                    ),
                    self.assertRaises(PublicationError),
                ):
                    publish_run_set(
                        run_set_dir,
                        root / "dataset",
                        PROJECT_ROOT,
                        source_commit=source_commit,
                    )

    def test_rejects_manifest_body_and_recorded_digest_tampering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_set_dir = self._write_run_set(root / "source", "run-001")
            source = {
                "git_commit": "c" * 40,
                "git_dirty": False,
                "worktree_digest": "d" * 64,
            }
            config = resolve_run_config(
                "java/spring-boot",
                "ping-api",
                "jvm-java25",
                PROJECT_ROOT,
                load_profile="platform-qualification-v1",
                environment_profile="home-k3s-v1",
                measurement_protocol="official-service-v1",
            )
            config = replace(
                config,
                image_tag=(
                    "ghcr.io/moseoh/hello-realworld-bench/spring-boot@sha256:"
                    + "e" * 64
                ),
            )
            manifest = build_resolved_manifest(config, "run-001", source)
            manifest["execution"]["target"]["endpoint"] = "/tampered"
            self._rehash_manifest(manifest)
            (run_set_dir / "resolved-manifest.json").write_text(json.dumps(manifest))

            run_set = json.loads((run_set_dir / "run-set.json").read_text())
            run_set["manifest_digest"] = manifest["manifest_digest"]
            run_set["cohort_fingerprint"] = manifest["cohort"]["fingerprint"]
            (run_set_dir / "run-set.json").write_text(json.dumps(run_set))
            trial_path = run_set_dir / "trials/01/trial.json"
            trial = json.loads(trial_path.read_text())
            trial["manifest_digest"] = manifest["manifest_digest"]
            trial["cohort_fingerprint"] = manifest["cohort"]["fingerprint"]
            trial_path.write_text(json.dumps(trial))

            with (
                patch("hrw_runner.publication.validate_run_set_evidence"),
                patch("hrw_runner.manifest.read_git_provenance", return_value=source),
                self.assertRaisesRegex(ValueError, "execution.target.endpoint"),
            ):
                publish_run_set(
                    run_set_dir,
                    root / "dataset",
                    PROJECT_ROOT,
                    source_commit="c" * 40,
                )

    def _rehash_manifest(self, manifest):
        cohort_payload = {
            key: value
            for key, value in manifest["cohort"].items()
            if key != "fingerprint"
        }
        manifest["cohort"]["fingerprint"] = self._digest(cohort_payload)
        manifest_payload = {
            key: value
            for key, value in manifest.items()
            if key != "manifest_digest"
        }
        manifest["manifest_digest"] = self._digest(manifest_payload)

    def _digest(self, value):
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _write_run_set(self, directory: Path, run_set_id: str) -> Path:
        trial_dir = directory / "trials/01"
        trial_dir.mkdir(parents=True)
        files = {
            directory / "preflight.json": {"valid": True},
            directory / "postflight.json": {"valid": True},
            directory / "build.json": {
                "image": "ghcr.io/example/target@sha256:" + "e" * 64,
                "digest": "sha256:" + "e" * 64,
            },
            trial_dir / "trial.json": {
                "trial_id": "trial-01",
                "status": "valid",
                "time_series": {"path": "time-series.json"},
                "artifact_manifest": {"path": "artifact-manifest.json"},
            },
            trial_dir / "result.json": {"run_id": run_set_id},
            trial_dir / "time-series.json": {"samples": []},
            trial_dir / "artifact-manifest.json": {"artifacts": []},
            trial_dir / "k6.log": {"raw": True},
        }
        for path, document in files.items():
            path.write_text(json.dumps(document))
        manifest = {
            "run_id": run_set_id,
            "manifest_digest": DIGEST_A,
            "source": {"git_commit": "c" * 40, "git_dirty": False},
            "selection": {
                "implementation": "java/spring-boot",
                "variant": "jvm-java25",
                "scenario": "ping-api",
                "load_profile": "platform-qualification-v1",
                "environment_profile": "home-k3s-v1",
                "measurement_protocol": "official-service-v1",
                "build_profile": "local-gradle-docker",
            },
            "cohort": {"fingerprint": DIGEST_B, "evidence_family": "service"},
        }
        (directory / "resolved-manifest.json").write_text(json.dumps(manifest))
        run_set = {
            "run_set_id": run_set_id,
            "run_id": run_set_id,
            "status": "complete",
            "started_at": "2026-07-13T00:00:00Z",
            "finished_at": "2026-07-13T00:10:00Z",
            "manifest_digest": DIGEST_A,
            "cohort_fingerprint": DIGEST_B,
            "expected_trials": 1,
            "trials": [
                {
                    "trial_id": "trial-01",
                    "index": 1,
                    "status": "valid",
                    "path": "trials/01/trial.json",
                    "sha256": "f" * 64,
                }
            ],
            "summary": {"trial_count": 1, "valid_trial_count": 1},
            "platform_evidence": {
                name: {"path": f"{name}.json", "sha256": "f" * 64}
                for name in ("preflight", "postflight", "build")
            },
        }
        (directory / "run-set.json").write_text(json.dumps(run_set))
        return directory

    def _write_build_run_set(self, directory: Path, run_set_id: str) -> Path:
        directory.mkdir(parents=True)
        for name in ("preflight.json", "postflight.json", "cache-seed.json"):
            (directory / name).write_text(json.dumps({"name": name}))
        for index in range(1, 4):
            trial_dir = directory / "trials" / f"{index:02d}"
            trial_dir.mkdir(parents=True)
            (trial_dir / "operation.log").write_text("raw operation log\n")
            (trial_dir / "artifact-manifest.json").write_text(
                json.dumps({"trial_id": f"trial-{index:02d}", "artifacts": []})
            )
            (trial_dir / "build-trial.json").write_text(
                json.dumps(
                    {
                        "trial_id": f"trial-{index:02d}",
                        "started_at": f"2026-07-13T00:0{index}:00Z",
                        "finished_at": f"2026-07-13T00:1{index}:00Z",
                        "artifact_manifest": {"path": "artifact-manifest.json"},
                    }
                )
            )
        manifest = {
            "run_id": run_set_id,
            "manifest_digest": DIGEST_A,
            "source": {"git_commit": "c" * 40, "git_dirty": False},
            "selection": {
                "implementation": "java/spring-boot",
                "variant": "jvm-java25",
                "environment_profile": "home-build-v1",
                "measurement_protocol": "official-build-v1",
                "build_profile": "official-gradle-docker-v1",
            },
            "cohort": {"fingerprint": DIGEST_B, "evidence_family": "build"},
        }
        (directory / "build-resolved-manifest.json").write_text(json.dumps(manifest))
        run_set = {
            "run_set_id": run_set_id,
            "run_id": run_set_id,
            "status": "complete",
            "manifest_digest": DIGEST_A,
            "cohort_fingerprint": DIGEST_B,
            "expected_trials": 3,
            "trials": [
                {
                    "trial_id": f"trial-{index:02d}",
                    "index": index,
                    "status": "valid",
                    "path": f"trials/{index:02d}/build-trial.json",
                    "sha256": "f" * 64,
                }
                for index in range(1, 4)
            ],
            "summary": {"trial_count": 3, "valid_trial_count": 3},
            "campaign_evidence": {
                name: {
                    "path": f"{name.replace('_', '-')}.json",
                    "sha256": "f" * 64,
                }
                for name in ("preflight", "postflight", "cache_seed")
            },
        }
        (directory / "build-run-set.json").write_text(json.dumps(run_set))
        return directory


if __name__ == "__main__":
    unittest.main()
