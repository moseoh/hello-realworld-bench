import json
import tempfile
import unittest
from pathlib import Path

from hrw_runner.summarize import collect_result_rows, filter_latest_rows, format_table


class SummarizeResultsTest(unittest.TestCase):
    def test_collects_result_rows_sorted_by_run_id_descending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(
                root_dir / "results/java/spring-boot/jvm-java25/ping-api/new/result.json",
                {
                    "run_id": "2026-07-05T10-00-00_java_spring-boot_jvm-java25_ping-api",
                    "scenario": "ping-api",
                    "implementation": "java/spring-boot",
                    "variant": "jvm-java25",
                    "startup": {"ready_ms": 1234},
                    "runtime_metrics": {
                        "rps": 1000.25,
                        "p95_ms": 2.5,
                        "error_rate": 0,
                        "cpu_percent": 151.25,
                        "memory_usage": "256MiB / 1GiB",
                    },
                },
            )
            self._write_result(
                root_dir / "results/java/spring-boot/jvm-java25/cold-start-api/old/result.json",
                {
                    "run_id": "2026-07-04T10-00-00_java_spring-boot_jvm-java25_cold-start-api",
                    "scenario": "cold-start-api",
                    "implementation": "java/spring-boot",
                    "variant": "jvm-java25",
                    "startup": {"ready_ms": 1400},
                    "runtime_metrics": {
                        "cpu_percent": 0.1,
                        "memory_usage": "180MiB / 1GiB",
                    },
                },
            )

            rows = collect_result_rows(root_dir)

        self.assertEqual([row["scenario"] for row in rows], ["ping-api", "cold-start-api"])
        self.assertEqual(rows[0]["ready_ms"], 1234)
        self.assertEqual(rows[0]["rps"], 1000.25)
        self.assertEqual(rows[1]["rps"], None)

    def test_formats_rows_as_table(self):
        table = format_table(
            [
                {
                    "run_id": "run-1",
                    "scenario": "ping-api",
                    "implementation": "java/spring-boot",
                    "variant": "jvm-java25",
                    "ready_ms": 1234,
                    "rps": 1000.25,
                    "p95_ms": 2.5,
                    "error_rate": 0,
                    "cpu_percent": 151.25,
                    "memory_usage": "256MiB / 1GiB",
                }
            ]
        )

        self.assertIn("scenario", table)
        self.assertIn("ping-api", table)
        self.assertIn("1000.25", table)
        self.assertIn("151.25", table)

    def test_filters_latest_row_per_scenario_implementation_and_variant(self):
        rows = [
            {
                "run_id": "2026-07-05T10-00-00_java_spring-boot_jvm-java25_ping-api",
                "scenario": "ping-api",
                "implementation": "java/spring-boot",
                "variant": "jvm-java25",
            },
            {
                "run_id": "2026-07-04T10-00-00_java_spring-boot_jvm-java25_ping-api",
                "scenario": "ping-api",
                "implementation": "java/spring-boot",
                "variant": "jvm-java25",
            },
            {
                "run_id": "2026-07-04T10-00-00_java_spring-boot_jvm-java25_cold-start-api",
                "scenario": "cold-start-api",
                "implementation": "java/spring-boot",
                "variant": "jvm-java25",
            },
        ]

        latest_rows = filter_latest_rows(rows)

        self.assertEqual(
            [row["run_id"] for row in latest_rows],
            [
                "2026-07-05T10-00-00_java_spring-boot_jvm-java25_ping-api",
                "2026-07-04T10-00-00_java_spring-boot_jvm-java25_cold-start-api",
            ],
        )

    def _write_result(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")


if __name__ == "__main__":
    unittest.main()
