import unittest

from hrw_runner.kubernetes_stats import normalize_stats_sample, validate_stats_series


class KubernetesStatsTest(unittest.TestCase):
    def test_normalizes_target_k6_host_and_background_resources(self):
        sample = normalize_stats_sample(
            {
                "node": {
                    "cpu": {"time": "2026-07-13T00:00:10Z", "usageNanoCores": 3_500_000_000},
                    "memory": {"workingSetBytes": 5_000_000_000},
                },
                "pods": [
                    self._pod("hrw-test", "target", 1_500_000_000, 500_000_000),
                    self._pod("hrw-test", "k6-measured-01-x", 1_000_000_000, 300_000_000),
                    self._pod("other", "app", 500_000_000, 100_000_000),
                ],
            },
            "hrw-test",
            10_200,
        )

        self.assertEqual(sample["source_time"], "2026-07-13T00:00:10Z")
        self.assertEqual(sample["target_cpu_percent"], 150.0)
        self.assertEqual(sample["load_generator_cpu_percent"], 100.0)
        self.assertEqual(sample["host_cpu_percent"], 350.0)
        self.assertEqual(sample["background_cpu_millicores"], 1000.0)
        self.assertEqual(sample["background_memory_bytes"], 4_200_000_000)

    def test_validates_coverage_and_background_thresholds(self):
        series = [
            {
                "source_time": f"t-{index}",
                "background_cpu_millicores": 500.0,
                "background_memory_bytes": 2_000_000_000,
                "load_generator_memory_bytes": 1,
                "load_generator_cpu_percent": 1.0,
            }
            for index in range(6)
        ]
        validity = {
            "stats_sample_interval_seconds": 10,
            "min_sample_coverage_ratio": 0.9,
            "max_background_cpu_millicores": 1000,
            "max_background_memory_bytes": 8_000_000_000,
        }

        valid = validate_stats_series(series, 60, validity)
        noisy = validate_stats_series(
            [*series, {**series[-1], "source_time": "t-6", "background_cpu_millicores": 1001}],
            60,
            validity,
        )

        self.assertEqual(valid["status"], "valid")
        self.assertEqual(valid["coverage_ratio"], 1.0)
        self.assertEqual(noisy["status"], "invalid")
        self.assertTrue(any("background CPU" in reason for reason in noisy["reasons"]))

    @staticmethod
    def _pod(namespace, name, cpu, memory):
        return {
            "podRef": {"namespace": namespace, "name": name},
            "containers": [
                {
                    "name": name,
                    "cpu": {"usageNanoCores": cpu},
                    "memory": {"workingSetBytes": memory},
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
