import unittest

from hrw_runner.results import docker_resource_metrics, k6_runtime_metrics


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


if __name__ == "__main__":
    unittest.main()
