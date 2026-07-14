import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BenchmarkEntrypointTest(unittest.TestCase):
    def test_java_images_use_the_same_pre_exec_marker_contract(self):
        cases = {
            "spring-boot": "exec java -jar /app/app.jar",
            "quarkus": "exec java -jar /app/quarkus-run.jar",
        }
        common_lines = [
            "#!/bin/sh",
            "set -eu",
            'started_ns="$(date +%s%N)"',
            'started="$((started_ns / 1000000))"',
            "printf 'HRW_ENTRYPOINT_PRE_EXEC_EPOCH_MS=%s\\n' \"$started\"",
        ]
        for implementation, expected_exec in cases.items():
            with self.subTest(implementation=implementation):
                directory = ROOT / "implementations/java" / implementation
                entrypoint = directory / "benchmark-entrypoint.sh"
                lines = [line for line in entrypoint.read_text().splitlines() if line]
                self.assertEqual(lines[:-1], common_lines)
                self.assertEqual(lines[-1], expected_exec)
                subprocess.run(["sh", "-n", entrypoint], check=True)
                dockerfile = (directory / "Dockerfile").read_text()
                self.assertEqual(
                    dockerfile.splitlines()[0],
                    "FROM eclipse-temurin:25-jre@sha256:"
                    "d0eb1b9018b3044da1b7346f39e945f71095749853d69a3aa16b8c99dad9bb45 "
                    "AS runtime-base",
                )
                self.assertIn(
                    'ENTRYPOINT ["/app/benchmark-entrypoint.sh"]', dockerfile
                )
                self.assertIn("COPY --chmod=755", dockerfile)
                dockerignore = directory / ".dockerignore"
                if dockerignore.is_file() and "*" in dockerignore.read_text().splitlines():
                    self.assertIn(
                        "!benchmark-entrypoint.sh", dockerignore.read_text().splitlines()
                    )


if __name__ == "__main__":
    unittest.main()
