import unittest
from pathlib import Path

from hrw_runner.config import resolve_run_config
from hrw_runner.runner import RESULT_SCHEMA_VERSION, _result_document


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
                "ready_ms": 1200,
                "first_request_ms": 7,
                "iterations": 1,
                "summary": {
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
            {"CPUPerc": "12.34%", "MemUsage": "128.5MiB / 1GiB", "MemPerc": "12.55%"},
        )

        self.assertEqual(result["schema_version"], RESULT_SCHEMA_VERSION)
        self.assertEqual(result["scenario"], "ping-api")
        self.assertEqual(result["build"]["clean_build_ms"], 1000)
        self.assertEqual(result["build"]["cache"]["docker_build_cache"], "enabled")
        self.assertEqual(result["startup"]["iterations"], 1)
        self.assertEqual(
            result["runtime_metrics"],
            {
                "rps": 123.4,
                "p50_ms": 4.5,
                "p95_ms": 12.3,
                "p99_ms": 45.6,
                "error_rate": 0,
                "cpu_percent": 12.34,
                "memory_usage": "128.5MiB / 1GiB",
                "memory_percent": 12.55,
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
                "ready_ms": 1200,
                "first_request_ms": 7,
                "iterations": 5,
                "summary": {
                    "ready_ms": {"min": 1000, "median": 1200, "p95": 1400, "max": 1400},
                    "first_request_ms": {"min": 4, "median": 6, "p95": 8, "max": 8},
                },
            },
            {"skipped": True, "reason": "load disabled for scenario"},
            {"CPUPerc": "12.34%", "MemUsage": "128.5MiB / 1GiB", "MemPerc": "12.55%"},
        )

        self.assertEqual(result["schema_version"], RESULT_SCHEMA_VERSION)
        self.assertEqual(result["scenario"], "cold-start-api")
        self.assertEqual(result["startup"]["iterations"], 5)
        self.assertEqual(
            result["runtime_metrics"],
            {
                "cpu_percent": 12.34,
                "memory_usage": "128.5MiB / 1GiB",
                "memory_percent": 12.55,
            },
        )


if __name__ == "__main__":
    unittest.main()
