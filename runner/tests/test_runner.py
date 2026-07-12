import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hrw_runner.config import resolve_run_config
from hrw_runner.runner import (
    RESULT_SCHEMA_VERSION,
    _compose_files,
    _dependency_services,
    _result_document,
    _run_k6,
)


class ResultDocumentTest(unittest.TestCase):
    def test_builds_result_document_for_load_test_scenario(self):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25", root_dir)

        result = _result_document(
            config,
            "run-id",
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
    def test_includes_variant_compose_before_scenario_compose(self):
        root_dir = Path(__file__).resolve().parents[2]
        config = resolve_run_config(
            "java/spring-boot",
            "io-aggregation-api",
            "jvm-java25-virtual-threads",
            root_dir,
        )

        names = [path.name for path in _compose_files(config, root_dir)]

        self.assertEqual(
            names,
            [
                "docker-compose.base.yml",
                "docker-compose.spring-boot.yml",
                "docker-compose.spring-boot.jvm-java25-virtual-threads.yml",
                "docker-compose.io-aggregation-api.yml",
            ],
        )


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
