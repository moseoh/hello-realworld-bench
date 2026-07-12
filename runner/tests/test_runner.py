import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hrw_runner.config import resolve_run_config
from hrw_runner.manifest import validate_resolved_manifest
from hrw_runner.runner import (
    RESULT_SCHEMA_VERSION,
    RunPaths,
    _compose_files,
    _dependency_services,
    _metadata,
    _result_document,
    _run_k6,
    run_benchmark,
)


MANIFEST_DIGEST = "a" * 64
COHORT_FINGERPRINT = "b" * 64


class ResultDocumentTest(unittest.TestCase):
    def test_builds_result_document_for_load_test_scenario(self):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25", root_dir)

        result = _result_document(
            config,
            "run-id",
            MANIFEST_DIGEST,
            COHORT_FINGERPRINT,
            {"os": "Darwin", "load_generator": "same-host"},
            {
                "clean_build_ms": 1000,
                "docker_build_ms": 2000,
                "image_size_mb": 300.5,
                "cache": {
                    "gradle_user_home": "implementation-local .gradle-cache",
                    "gradle_dependency_cache": "persistent",
                    "docker_build_cache": "enabled",
                    "docker_build_input": "prebuilt application artifact",
                },
            },
            {
                "dependency_ready_ms": 0,
                "ready_ms": 1200,
                "first_request_ms": 7,
                "iterations": 1,
                "summary": {
                    "dependency_ready_ms": {"min": 0, "median": 0, "p95": 0, "max": 0},
                    "ready_ms": {"min": 1200, "median": 1200, "p95": 1200, "max": 1200},
                    "first_request_ms": {"min": 7, "median": 7, "p95": 7, "max": 7},
                },
            },
            {
                "metrics": {
                    "http_reqs": {"rate": 123.4},
                    "http_req_duration": {"med": 4.5, "p(95)": 12.3, "p(99)": 45.6},
                    "http_req_failed": {"value": 0},
                }
            },
            {
                "samples": [
                    {"CPUPerc": "10.00%", "MemUsage": "100MiB / 1GiB", "MemPerc": "10.00%"},
                    {"CPUPerc": "12.34%", "MemUsage": "128.5MiB / 1GiB", "MemPerc": "12.55%"},
                ]
            },
        )

        self.assertEqual(result["schema_version"], RESULT_SCHEMA_VERSION)
        self.assertEqual(result["manifest_digest"], MANIFEST_DIGEST)
        self.assertEqual(result["cohort_fingerprint"], COHORT_FINGERPRINT)
        self.assertEqual(result["scenario"], "ping-api")
        self.assertNotIn("language", config.runtime)
        self.assertNotIn("framework", config.runtime)
        self.assertEqual(result["runtime"]["language"], "java")
        self.assertEqual(result["runtime"]["framework"], "spring-boot")
        self.assertEqual(result["build"]["clean_build_ms"], 1000)
        self.assertEqual(result["build"]["cache"]["docker_build_cache"], "enabled")
        self.assertEqual(result["startup"]["dependency_ready_ms"], 0)
        self.assertEqual(result["startup"]["iterations"], 1)
        self.assertEqual(
            result["runtime_metrics"],
            {
                "rps": 123.4,
                "p50_ms": 4.5,
                "p95_ms": 12.3,
                "p99_ms": 45.6,
                "error_rate": 0,
                "cpu_percent": 11.17,
                "cpu_percent_avg": 11.17,
                "cpu_percent_max": 12.34,
                "memory_usage": "128.5MiB / 1GiB",
                "memory_usage_max": "128.5MiB / 1GiB",
                "memory_usage_max_bytes": 134742016,
                "memory_percent": 11.275,
                "memory_percent_avg": 11.275,
                "memory_percent_max": 12.55,
            },
        )

    def test_builds_result_document_for_load_disabled_scenario(self):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config(
            "java/spring-boot",
            "cold-start-api",
            "jvm-java25",
            root_dir,
        )

        result = _result_document(
            config,
            "run-id",
            MANIFEST_DIGEST,
            COHORT_FINGERPRINT,
            {"os": "Darwin", "load_generator": "same-host"},
            {
                "clean_build_ms": 1000,
                "docker_build_ms": 2000,
                "image_size_mb": 300.5,
                "cache": {
                    "gradle_user_home": "implementation-local .gradle-cache",
                    "gradle_dependency_cache": "persistent",
                    "docker_build_cache": "enabled",
                    "docker_build_input": "prebuilt application artifact",
                },
            },
            {
                "dependency_ready_ms": 0,
                "ready_ms": 1200,
                "first_request_ms": 7,
                "iterations": 5,
                "summary": {
                    "dependency_ready_ms": {"min": 0, "median": 0, "p95": 0, "max": 0},
                    "ready_ms": {"min": 1000, "median": 1200, "p95": 1400, "max": 1400},
                    "first_request_ms": {"min": 4, "median": 6, "p95": 8, "max": 8},
                },
            },
            {"skipped": True, "reason": "load disabled for scenario"},
            {"CPUPerc": "12.34%", "MemUsage": "128.5MiB / 1GiB", "MemPerc": "12.55%"},
        )

        self.assertEqual(result["schema_version"], RESULT_SCHEMA_VERSION)
        self.assertEqual(result["scenario"], "cold-start-api")
        self.assertEqual(result["startup"]["dependency_ready_ms"], 0)
        self.assertEqual(result["startup"]["iterations"], 5)
        self.assertEqual(
            result["runtime_metrics"],
            {
                "cpu_percent": 12.34,
                "memory_usage": "128.5MiB / 1GiB",
                "memory_percent": 12.55,
            },
        )


class StartupDependencyTest(unittest.TestCase):
    def test_finds_enabled_scenario_dependency_services(self):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config(
            "java/spring-boot",
            "transactional-command-api",
            "jvm-java25",
            root_dir,
        )

        self.assertEqual(_dependency_services(config), ["postgres"])

    def test_omits_disabled_dependency_services(self):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25", root_dir)

        self.assertEqual(_dependency_services(config), [])

    def test_finds_mock_upstream_dependency_service(self):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config(
            "java/spring-boot",
            "io-aggregation-api",
            "jvm-java25",
            root_dir,
        )

        self.assertEqual(_dependency_services(config), ["mock-upstream"])


class ComposeFilesTest(unittest.TestCase):
    def test_uses_manifest_compose_assets_in_role_order(self):
        root_dir = Path(__file__).resolve().parents[2]
        manifest = {
            "assets": [
                {
                    "role": "scenario-file",
                    "path": "scenarios/io-aggregation-api/k6.js",
                },
                {
                    "role": "scenario-compose",
                    "path": "infra/docker-compose.io-aggregation-api.yml",
                },
                {"role": "environment-compose", "path": "infra/docker-compose.base.yml"},
                {
                    "role": "variant-compose",
                    "path": "infra/docker-compose.spring-boot.jvm-java25-virtual-threads.yml",
                },
                {
                    "role": "implementation-compose",
                    "path": "infra/docker-compose.spring-boot.yml",
                },
            ]
        }

        names = [path.name for path in _compose_files(manifest, root_dir)]

        self.assertEqual(
            names,
            [
                "docker-compose.base.yml",
                "docker-compose.spring-boot.yml",
                "docker-compose.spring-boot.jvm-java25-virtual-threads.yml",
                "docker-compose.io-aggregation-api.yml",
            ],
        )

    def test_rejects_missing_non_compose_and_unsafe_manifest_paths(self):
        root_dir = Path(__file__).resolve().parents[2]
        cases = (
            {"role": "environment-compose", "path": "infra/missing.yml"},
            {"role": "environment-compose", "path": "README.md"},
            {"role": "environment-compose", "path": "../outside.yml"},
        )

        for asset in cases:
            with self.subTest(asset=asset):
                with self.assertRaisesRegex(ValueError, "compose asset"):
                    _compose_files({"assets": [asset]}, root_dir)


class ManifestRunFlowTest(unittest.TestCase):
    def setUp(self):
        self.root_dir = Path(__file__).resolve().parents[2]
        self.config = resolve_run_config(
            "java/spring-boot", "cold-start-api", "jvm-java25", self.root_dir
        )

    @patch("hrw_runner.runner._write_target_log")
    @patch("hrw_runner.runner._docker_stats", return_value={})
    @patch("hrw_runner.runner._measure_startup", return_value={})
    @patch("hrw_runner.runner._measure_build", return_value={})
    @patch("hrw_runner.runner._compose")
    @patch("hrw_runner.runner.shutil.which", return_value="/usr/local/bin/docker")
    def test_writes_valid_manifest_before_first_measurement_and_cross_references_outputs(
        self, _which, compose, measure_build, _measure_startup, _docker_stats, _target_log
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir) / "result"

            def assert_manifest_written(_config, _log):
                manifest = json.loads((result_dir / "resolved-manifest.json").read_text())
                validate_resolved_manifest(manifest, self.root_dir)
                return {}

            measure_build.side_effect = assert_manifest_written
            with patch(
                "hrw_runner.runner._run_paths",
                return_value=RunPaths(result_dir=result_dir, run_id="run-id"),
            ):
                run_benchmark(self.config, self.root_dir)

            manifest = json.loads((result_dir / "resolved-manifest.json").read_text())
            metadata = json.loads((result_dir / "metadata.json").read_text())
            result = json.loads((result_dir / "result.json").read_text())

        self.assertTrue(measure_build.called)
        self.assertEqual(metadata["manifest_digest"], manifest["manifest_digest"])
        self.assertEqual(metadata["cohort_fingerprint"], manifest["cohort"]["fingerprint"])
        self.assertEqual(result["manifest_digest"], manifest["manifest_digest"])
        self.assertEqual(result["cohort_fingerprint"], manifest["cohort"]["fingerprint"])
        self.assertEqual(result["schema_version"], "0.2")

    @patch("hrw_runner.runner._measure_build")
    @patch("hrw_runner.runner._compose")
    @patch("hrw_runner.runner.validate_resolved_manifest", side_effect=ValueError("invalid"))
    @patch("hrw_runner.runner.shutil.which", return_value="/usr/local/bin/docker")
    def test_manifest_validation_failure_prevents_measurement(
        self, _which, _validate_manifest, compose, measure_build
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir) / "result"
            with patch(
                "hrw_runner.runner._run_paths",
                return_value=RunPaths(result_dir=result_dir, run_id="run-id"),
            ):
                with self.assertRaisesRegex(ValueError, "invalid"):
                    run_benchmark(self.config, self.root_dir)

        compose.assert_not_called()
        measure_build.assert_not_called()


class MetadataTest(unittest.TestCase):
    @patch("hrw_runner.runner.environment_metadata", return_value={})
    def test_references_manifest_fingerprints(self, _environment_metadata):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25", root_dir)

        metadata = _metadata(config, "run-id", MANIFEST_DIGEST, COHORT_FINGERPRINT)

        self.assertEqual(metadata["manifest_digest"], MANIFEST_DIGEST)
        self.assertEqual(metadata["cohort_fingerprint"], COHORT_FINGERPRINT)


class ScenarioScriptPathTest(unittest.TestCase):
    def setUp(self):
        self.root_dir = Path(__file__).resolve().parents[2]
        self.config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            "jvm-java25",
            self.root_dir,
        )

    @patch("hrw_runner.runner._run_logged")
    @patch("hrw_runner.runner.shutil.which", return_value="/usr/local/bin/k6")
    def test_run_k6_rejects_scripts_outside_scenario_or_not_regular_js_files(
        self, _which, _run_logged
    ):
        for script in (
            "scenarios/io-aggregation-api/k6.js",
            "scenarios/ping-api/README.md",
            "scenarios/ping-api/missing.js",
        ):
            with self.subTest(script=script):
                config = replace(
                    self.config,
                    load={**self.config.load, "script": script},
                )

                with self.assertRaisesRegex(SystemExit, "Invalid scenario k6 script"):
                    _run_k6(
                        self.root_dir,
                        config,
                        "1s",
                        self.root_dir / "summary.json",
                        io.StringIO(),
                    )

    @patch("hrw_runner.runner._run_logged")
    @patch("hrw_runner.runner.shutil.which", return_value="/usr/local/bin/k6")
    def test_run_k6_rejects_scenario_script_symlink_escape(
        self, _which, run_logged
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            scenario_dir = root_dir / "scenarios/ping-api"
            scenario_dir.mkdir(parents=True)
            outside_script = root_dir / "outside.js"
            outside_script.write_text("export default function () {}\n")
            script = scenario_dir / "escape.js"
            try:
                script.symlink_to(outside_script)
            except OSError as error:
                self.skipTest(f"symlinks are not available: {error}")
            config = replace(
                self.config,
                root_dir=root_dir,
                scenario_dir=scenario_dir,
                load={**self.config.load, "script": "scenarios/ping-api/escape.js"},
            )

            with self.assertRaisesRegex(SystemExit, "Invalid scenario k6 script"):
                _run_k6(
                    root_dir,
                    config,
                    "1s",
                    root_dir / "summary.json",
                    io.StringIO(),
                )

        run_logged.assert_not_called()

    @patch("hrw_runner.runner._run_logged")
    @patch("hrw_runner.runner.shutil.which", return_value="/usr/local/bin/k6")
    def test_local_k6_uses_validated_scenario_script(self, _which, run_logged):
        summary_path = self.root_dir / "summary.json"

        _run_k6(
            self.root_dir,
            self.config,
            "1s",
            summary_path,
            io.StringIO(),
        )

        args = run_logged.call_args.args[0]
        self.assertEqual(
            args[-1],
            str((self.root_dir / "scenarios/ping-api/k6.js").resolve()),
        )

    @patch("hrw_runner.runner._run_logged")
    @patch("hrw_runner.runner.shutil.which", return_value=None)
    def test_docker_k6_uses_validated_scenario_script(self, _which, run_logged):
        _run_k6(
            self.root_dir,
            self.config,
            "1s",
            self.root_dir / "summary.json",
            io.StringIO(),
        )

        args = run_logged.call_args.args[0]
        self.assertEqual(args[-1], "/work/scenarios/ping-api/k6.js")


if __name__ == "__main__":
    unittest.main()
