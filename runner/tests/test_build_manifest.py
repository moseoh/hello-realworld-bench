import copy
import hashlib
import json
import unittest
from pathlib import Path

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
        self.assertEqual(manifest["cohort"]["contracts"], manifest["contracts"])
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


if __name__ == "__main__":
    unittest.main()
