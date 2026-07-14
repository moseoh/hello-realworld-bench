import copy
import hashlib
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from hrw_runner.build_config import resolve_build_run_config
from hrw_runner.build_manifest import (
    ManifestValidationError,
    build_resolved_build_manifest,
    validate_resolved_build_manifest,
)
from hrw_runner.manifest import read_git_provenance


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class BuildResolvedManifestTest(unittest.TestCase):
    def setUp(self):
        self.config = resolve_build_run_config(
            "java/spring-boot",
            "jvm-java25",
            PROJECT_ROOT,
            environment_profile="home-build-v1",
            measurement_protocol="official-build-v1",
            build_profile="official-gradle-docker-v1",
        )
        self.source = read_git_provenance(PROJECT_ROOT)

    def test_builds_a_build_only_manifest_with_stable_digests(self):
        manifest = build_resolved_build_manifest(self.config, "build-001", self.source)

        self.assertEqual(
            set(manifest["selection"]),
            {
                "implementation",
                "variant",
                "environment_profile",
                "measurement_protocol",
                "build_profile",
            },
        )
        self.assertNotIn("scenario", manifest["selection"])
        self.assertNotIn("load_profile", manifest["selection"])
        self.assertNotIn("runtime", manifest["execution"])
        self.assertEqual(manifest["cohort"]["contracts"], manifest["contracts"])
        self.assertEqual(manifest["cohort"]["evidence_family"], "build")
        self.assertEqual(
            manifest["cohort"]["fingerprint"],
            _digest({key: value for key, value in manifest["cohort"].items() if key != "fingerprint"}),
        )
        self.assertEqual(
            manifest["manifest_digest"],
            _digest({key: value for key, value in manifest.items() if key != "manifest_digest"}),
        )
        validate_resolved_build_manifest(manifest, PROJECT_ROOT)

    def test_validation_rejects_recomputed_checkout_bound_tampering(self):
        manifest = build_resolved_build_manifest(self.config, "build-001", self.source)
        tampered = copy.deepcopy(manifest)
        tampered["contracts"]["variant"]["path"] = (
            "implementations/java/spring-boot/variants/jvm-java25-virtual-threads.yaml"
        )
        tampered["cohort"]["contracts"] = copy.deepcopy(tampered["contracts"])
        tampered["cohort"]["fingerprint"] = _digest(
            {key: value for key, value in tampered["cohort"].items() if key != "fingerprint"}
        )
        tampered["manifest_digest"] = _digest(
            {key: value for key, value in tampered.items() if key != "manifest_digest"}
        )

        with self.assertRaises(ManifestValidationError) as context:
            validate_resolved_build_manifest(tampered, PROJECT_ROOT)

        self.assertIn("$.contracts.variant", str(context.exception))

    def test_build_evidence_schemas_require_three_ordered_trial_references_and_metrics(self):
        trial = {
            "schema_version": "1.0",
            "run_id": "build-001",
            "trial_id": "trial-1",
            "manifest_digest": "a" * 64,
            "cohort_fingerprint": "b" * 64,
            "status": "valid",
            "started_at": "2026-07-14T00:00:00Z",
            "finished_at": "2026-07-14T00:01:00Z",
            "metrics": self._trial_metrics(),
        }
        run_set = {
            "schema_version": "1.0",
            "run_set_id": "build-set-001",
            "run_id": "build-001",
            "manifest_digest": "a" * 64,
            "cohort_fingerprint": "b" * 64,
            "status": "complete",
            "expected_trials": 3,
            "trials": [
                {
                    "trial_id": f"trial-{index:02d}",
                    "index": index,
                    "status": "valid",
                    "path": f"trials/{index:02d}/build-trial.json",
                    "sha256": chr(96 + index) * 64,
                }
                for index in range(1, 4)
            ],
            "summary": {
                "trial_count": 3,
                "valid_trial_count": 3,
                "build_metrics": self._build_metrics(),
            },
        }

        self.assertEqual(self._schema_errors("build-trial.schema.json", trial), [])
        self.assertEqual(self._schema_errors("build-run-set.schema.json", run_set), [])

        incomplete = copy.deepcopy(run_set)
        incomplete["trials"].pop()
        errors = self._schema_errors("build-run-set.schema.json", incomplete)
        self.assertTrue(errors)
        self.assertTrue(any(list(error.absolute_path) == ["trials"] for error in errors))

        wrong_expected_count = copy.deepcopy(run_set)
        wrong_expected_count["expected_trials"] = 2
        errors = self._schema_errors("build-run-set.schema.json", wrong_expected_count)
        self.assertTrue(
            any(list(error.absolute_path) == ["expected_trials"] for error in errors)
        )

        wrong_reference_index = copy.deepcopy(run_set)
        wrong_reference_index["trials"][2]["index"] = 4
        errors = self._schema_errors("build-run-set.schema.json", wrong_reference_index)
        self.assertTrue(any(list(error.absolute_path) == ["trials", 2, "index"] for error in errors))

        invalid_status = copy.deepcopy(run_set)
        invalid_status["trials"][1]["status"] = "invalid"
        errors = self._schema_errors("build-run-set.schema.json", invalid_status)
        self.assertTrue(any(list(error.absolute_path) == ["trials", 1, "status"] for error in errors))

        wrong_trial_id = copy.deepcopy(run_set)
        wrong_trial_id["trials"][2]["trial_id"] = "trial-04"
        errors = self._schema_errors("build-run-set.schema.json", wrong_trial_id)
        self.assertTrue(any(list(error.absolute_path) == ["trials", 2, "trial_id"] for error in errors))

        wrong_trial_path = copy.deepcopy(run_set)
        wrong_trial_path["trials"][1]["path"] = "trials/02/trial.json"
        errors = self._schema_errors("build-run-set.schema.json", wrong_trial_path)
        self.assertTrue(any(list(error.absolute_path) == ["trials", 1, "path"] for error in errors))

        wrong_summary_trial_id = copy.deepcopy(run_set)
        wrong_summary_trial_id["summary"]["build_metrics"]["image_rebuild_ms"]["trials"][2]["trial_id"] = "trial-04"
        errors = self._schema_errors("build-run-set.schema.json", wrong_summary_trial_id)
        self.assertTrue(
            any(
                list(error.absolute_path)
                == [
                    "summary",
                    "build_metrics",
                    "image_rebuild_ms",
                    "trials",
                    2,
                    "trial_id",
                ]
                for error in errors
            )
        )

        non_build_trial = copy.deepcopy(trial)
        non_build_trial["time_series"] = {"runtime_ms": 1}
        errors = self._schema_errors("build-trial.schema.json", non_build_trial)
        self.assertTrue(any(list(error.absolute_path) == [] for error in errors))

        non_build_run_set = copy.deepcopy(run_set)
        non_build_run_set["scenario"] = "ping-api"
        errors = self._schema_errors("build-run-set.schema.json", non_build_run_set)
        self.assertTrue(any(list(error.absolute_path) == [] for error in errors))

    def _trial_metrics(self):
        return {
            "gradle_clean_build_ms": 1,
            "gradle_incremental_rebuild_ms": 1,
            "image_package_ms": 1,
            "image_rebuild_ms": 1,
        }

    def _build_metrics(self):
        return {
            name: {
                "min": 1,
                "median": 1,
                "max": 1,
                "trials": [
                    {"trial_id": f"trial-{index:02d}", "value": 1}
                    for index in range(1, 4)
                ],
            }
            for name in self._trial_metrics()
        }

    def _schema_errors(self, name: str, value: object):
        schema = json.loads((PROJECT_ROOT / "contracts/schemas" / name).read_text())
        return list(Draft202012Validator(schema).iter_errors(value))


if __name__ == "__main__":
    unittest.main()
