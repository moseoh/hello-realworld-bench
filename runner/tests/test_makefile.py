import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MakefileRunTest(unittest.TestCase):
    def test_run_leaves_variant_and_profile_defaults_empty(self):
        makefile = (PROJECT_ROOT / "Makefile").read_text()

        self.assertIn("VARIANT ?=\n", makefile)
        self.assertIn("LOAD_PROFILE ?=\n", makefile)
        self.assertIn("ENVIRONMENT_PROFILE ?=\n", makefile)
        self.assertIn("MEASUREMENT_PROTOCOL ?=\n", makefile)
        self.assertIn("BUILD_PROFILE ?=\n", makefile)
        self.assertEqual(
            self._dry_run(),
            [
                "PYTHONPATH=runner",
                "uv",
                "run",
                "--project",
                "runner",
                "python",
                "-m",
                "hrw_runner",
                "java/spring-boot",
                "ping-api",
            ],
        )

    def test_run_passes_only_non_empty_variant_and_profile_values(self):
        self.assertEqual(
            self._dry_run(
                "VARIANT=jvm-java25",
                "LOAD_PROFILE=none",
                "ENVIRONMENT_PROFILE=local-docker-compose",
                "MEASUREMENT_PROTOCOL=development-service",
                "BUILD_PROFILE=local-gradle-docker",
            ),
            [
                "PYTHONPATH=runner",
                "uv",
                "run",
                "--project",
                "runner",
                "python",
                "-m",
                "hrw_runner",
                "java/spring-boot",
                "ping-api",
                "jvm-java25",
                "--load-profile",
                "none",
                "--environment-profile",
                "local-docker-compose",
                "--measurement-protocol",
                "development-service",
                "--build-profile",
                "local-gradle-docker",
            ],
        )

    def _dry_run(self, *variables: str) -> list[str]:
        completed = subprocess.run(
            ["make", "--no-print-directory", "-n", "run", *variables],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.split()


if __name__ == "__main__":
    unittest.main()
