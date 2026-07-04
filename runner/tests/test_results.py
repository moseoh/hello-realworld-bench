import unittest

from hrw_runner.results import (
    docker_resource_metrics,
    k6_runtime_metrics,
    summarize_startup_samples,
)


class MetricsExtractionTest(unittest.TestCase):
    def test_extracts_k6_http_metrics(self):
        summary = {
            "metrics": {
                "http_reqs": {"rate": 123.4},
                "http_req_duration": {
                    "med": 4.5,
                    "p(95)": 12.3,
                    "p(99)": 45.6,
                },
                "http_req_failed": {"rate": 0.01},
            }
        }

        self.assertEqual(
            k6_runtime_metrics(summary),
            {
                "rps": 123.4,
                "p50_ms": 4.5,
                "p95_ms": 12.3,
                "p99_ms": 45.6,
                "error_rate": 0.01,
            },
        )

    def test_extracts_k6_error_rate_from_value(self):
        summary = {
            "metrics": {
                "http_reqs": {"rate": 123.4},
                "http_req_duration": {
                    "med": 4.5,
                    "p(95)": 12.3,
                    "p(99)": 45.6,
                },
                "http_req_failed": {"value": 0},
            }
        }

        self.assertEqual(k6_runtime_metrics(summary)["error_rate"], 0)

    def test_omits_k6_metrics_when_load_is_skipped(self):
        self.assertEqual(
            k6_runtime_metrics({"skipped": True, "reason": "load disabled for scenario"}),
            {},
        )

    def test_extracts_docker_resource_metrics(self):
        stats = {
            "CPUPerc": "12.34%",
            "MemUsage": "128.5MiB / 1GiB",
            "MemPerc": "12.55%",
        }

        self.assertEqual(
            docker_resource_metrics(stats),
            {
                "cpu_percent": 12.34,
                "memory_usage": "128.5MiB / 1GiB",
                "memory_percent": 12.55,
            },
        )

    def test_summarizes_sampled_docker_resource_metrics(self):
        stats = {
            "samples": [
                {
                    "CPUPerc": "10.00%",
                    "MemUsage": "100MiB / 1GiB",
                    "MemPerc": "10.00%",
                },
                {
                    "CPUPerc": "30.00%",
                    "MemUsage": "200MiB / 1GiB",
                    "MemPerc": "20.00%",
                },
            ]
        }

        self.assertEqual(
            docker_resource_metrics(stats),
            {
                "cpu_percent": 20.0,
                "cpu_percent_avg": 20.0,
                "cpu_percent_max": 30.0,
                "memory_usage": "200MiB / 1GiB",
                "memory_usage_max": "200MiB / 1GiB",
                "memory_usage_max_bytes": 209715200,
                "memory_percent": 15.0,
                "memory_percent_avg": 15.0,
                "memory_percent_max": 20.0,
            },
        )

    def test_summarizes_startup_samples(self):
        samples = [
            {"ready_ms": 1200, "first_request_ms": 5},
            {"ready_ms": 1000, "first_request_ms": 4},
            {"ready_ms": 1400, "first_request_ms": 8},
            {"ready_ms": 1100, "first_request_ms": 6},
            {"ready_ms": 1300, "first_request_ms": 7},
        ]

        self.assertEqual(
            summarize_startup_samples(samples),
            {
                "ready_ms": {
                    "min": 1000,
                    "median": 1200,
                    "p95": 1400,
                    "max": 1400,
                },
                "first_request_ms": {
                    "min": 4,
                    "median": 6,
                    "p95": 8,
                    "max": 8,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
