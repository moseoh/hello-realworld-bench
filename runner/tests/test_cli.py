import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hrw_runner.__main__ import main
from hrw_runner.contracts import validate_repository_contracts


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CliTest(unittest.TestCase):
    def test_publish_passes_trusted_provenance_to_dataset_publisher(self):
        output = io.StringIO()
        with (
            patch("pathlib.Path.cwd", return_value=PROJECT_ROOT),
            patch("hrw_runner.__main__.publish_run_set") as publish,
            redirect_stdout(output),
        ):
            publish.return_value = PROJECT_ROOT / "dataset/run-sets/cohort/run-001"
            exit_code = main(
                [
                    "publish",
                    "results/run-001",
                    "dataset",
                    "--source-commit",
                    "c" * 40,
                    "--workflow-url",
                    "https://github.com/example/actions/runs/1",
                    "--raw-artifact-url",
                    "https://github.com/example/actions/runs/1#artifacts",
                    "--raw-artifact-sha256",
                    "a" * 64,
                ]
            )

        self.assertEqual(exit_code, 0)
        publish.assert_called_once_with(
            PROJECT_ROOT / "results/run-001",
            PROJECT_ROOT / "dataset",
            PROJECT_ROOT,
            source_commit="c" * 40,
            workflow_url="https://github.com/example/actions/runs/1",
            raw_artifact_url="https://github.com/example/actions/runs/1#artifacts",
            raw_artifact_sha256="a" * 64,
        )
        self.assertIn("Published dataset entry:", output.getvalue())

    def test_publish_rejects_missing_required_arguments(self):
        errors = io.StringIO()
        with redirect_stderr(errors):
            exit_code = main(["publish", "results/run-001", "dataset"])

        self.assertEqual(exit_code, 2)
        self.assertIn("Usage: python -m hrw_runner publish", errors.getvalue())

    def test_validate_prints_validated_contract_file_count(self):
        output = io.StringIO()
        with patch("pathlib.Path.cwd", return_value=PROJECT_ROOT), redirect_stdout(output):
            exit_code = main(["validate"])

        expected_count = len(validate_repository_contracts(PROJECT_ROOT))
        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), f"Validated {expected_count} contract files.\n")

    def test_validate_prints_aggregated_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            shutil.copytree(PROJECT_ROOT / "contracts", root_dir / "contracts")
            (root_dir / "contracts/load-profiles/malformed.yaml").write_text("id: [\n")
            (root_dir / "contracts/build-profiles/invalid-schema.yaml").write_text(
                "id: invalid-schema\n"
            )
            scenario_path = root_dir / "scenarios/ping-api/scenario.yaml"
            scenario_path.parent.mkdir(parents=True)
            scenario_path.write_text(
                (PROJECT_ROOT / "scenarios/ping-api/scenario.yaml")
                .read_text()
                .replace("load_profile: development-local", "load_profile: missing")
            )

            errors = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stderr(errors):
                exit_code = main(["validate"])

        self.assertEqual(exit_code, 1)
        self.assertIn("contracts/load-profiles/malformed.yaml", errors.getvalue())
        self.assertIn("invalid YAML", errors.getvalue())
        self.assertIn("contracts/build-profiles/invalid-schema.yaml", errors.getvalue())
        self.assertIn("'schema_version' is a required property", errors.getvalue())
        self.assertIn("scenarios/ping-api/scenario.yaml", errors.getvalue())
        self.assertIn("missing load-profile 'missing'", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_validate_does_not_hide_programming_errors(self):
        with patch(
            "hrw_runner.__main__.validate_repository_contracts",
            side_effect=RuntimeError("programming error"),
        ):
            with self.assertRaisesRegex(RuntimeError, "programming error"):
                main(["validate"])

    def test_summarize_prints_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize"])

        self.assertEqual(exit_code, 0)
        self.assertIn("scenario", output.getvalue())
        self.assertIn("ping-api", output.getvalue())

    def test_summarize_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"scenario": "ping-api"', output.getvalue())

    def test_summarize_prints_latest_only_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)
            self._write_old_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize", "--latest-only"])

        self.assertEqual(exit_code, 0)
        self.assertIn("1000.25", output.getvalue())
        self.assertNotIn("500.25", output.getvalue())

    def test_summarize_prints_latest_only_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)
            self._write_old_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize", "--latest-only", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"rps": 1000.25', output.getvalue())
        self.assertNotIn('"rps": 500.25', output.getvalue())

    def test_run_preserves_existing_positional_arguments(self):
        config = object()
        output = io.StringIO()
        with (
            patch("pathlib.Path.cwd", return_value=PROJECT_ROOT),
            patch("hrw_runner.__main__.resolve_run_config", return_value=config) as resolve,
            patch(
                "hrw_runner.__main__.run_benchmark",
                return_value=PROJECT_ROOT / "results/run",
            ) as run,
            redirect_stdout(output),
        ):
            exit_code = main(["java/spring-boot", "ping-api", "jvm-java25"])

        self.assertEqual(exit_code, 0)
        resolve.assert_called_once_with(
            "java/spring-boot",
            "ping-api",
            "jvm-java25",
            PROJECT_ROOT,
            load_profile=None,
            environment_profile=None,
            measurement_protocol=None,
            build_profile=None,
        )
        run.assert_called_once_with(config, PROJECT_ROOT)
        self.assertIn("Result directory:", output.getvalue())

    def test_run_set_uses_the_resolved_measurement_protocol(self):
        config = object()
        output = io.StringIO()
        with (
            patch("pathlib.Path.cwd", return_value=PROJECT_ROOT),
            patch("hrw_runner.__main__.resolve_run_config", return_value=config),
            patch(
                "hrw_runner.__main__.run_benchmark_set",
                return_value=PROJECT_ROOT / "results/run-set",
            ) as run_set,
            redirect_stdout(output),
        ):
            exit_code = main(
                ["run-set", "java/spring-boot", "ping-api", "jvm-java25"]
            )

        self.assertEqual(exit_code, 0)
        run_set.assert_called_once_with(config, PROJECT_ROOT)
        self.assertIn("Run set directory:", output.getvalue())

    def test_build_set_uses_only_build_selection_flags(self):
        config = object()
        output = io.StringIO()
        with (
            patch("pathlib.Path.cwd", return_value=PROJECT_ROOT),
            patch(
                "hrw_runner.__main__.resolve_build_run_config",
                return_value=config,
            ) as resolve,
            patch(
                "hrw_runner.__main__.run_build_benchmark_set",
                return_value=PROJECT_ROOT / "results/build/run-set",
            ) as run_set,
            redirect_stdout(output),
        ):
            exit_code = main(
                [
                    "build-set",
                    "java/spring-boot",
                    "jvm-java25",
                    "--environment-profile",
                    "home-build-v1",
                    "--measurement-protocol",
                    "official-build-v1",
                    "--build-profile",
                    "official-gradle-docker-v1",
                ]
            )

        self.assertEqual(exit_code, 0)
        resolve.assert_called_once_with(
            "java/spring-boot",
            "jvm-java25",
            PROJECT_ROOT,
            environment_profile="home-build-v1",
            measurement_protocol="official-build-v1",
            build_profile="official-gradle-docker-v1",
        )
        run_set.assert_called_once_with(config)
        self.assertIn("Build run set directory:", output.getvalue())

    def test_build_set_rejects_scenario_and_load_profile_arguments(self):
        for arguments in (
            ["build-set", "java/spring-boot", "ping-api"],
            [
                "build-set",
                "java/spring-boot",
                "--load-profile",
                "steady",
            ],
        ):
            with self.subTest(arguments=arguments):
                errors = io.StringIO()
                with redirect_stderr(errors):
                    exit_code = main(arguments)

                self.assertEqual(exit_code, 2)
                self.assertIn("build-set", errors.getvalue())

    def test_build_set_rejects_each_duplicate_profile_flag(self):
        required = {
            "--environment-profile": "home-build-v1",
            "--measurement-protocol": "official-build-v1",
            "--build-profile": "official-gradle-docker-v1",
        }
        for duplicate_flag in required:
            with self.subTest(duplicate_flag=duplicate_flag):
                arguments = ["build-set", "java/spring-boot", "jvm-java25"]
                for flag, value in required.items():
                    arguments.extend([flag, value])
                    if flag == duplicate_flag:
                        arguments.extend([flag, value])
                errors = io.StringIO()
                with redirect_stderr(errors):
                    exit_code = main(arguments)

                self.assertEqual(exit_code, 2)
                self.assertIn("build-set", errors.getvalue())

    def test_run_passes_all_profile_flags_after_the_optional_variant(self):
        config = object()
        output = io.StringIO()
        with (
            patch("pathlib.Path.cwd", return_value=PROJECT_ROOT),
            patch("hrw_runner.__main__.resolve_run_config", return_value=config) as resolve,
            patch(
                "hrw_runner.__main__.run_benchmark",
                return_value=PROJECT_ROOT / "results/run",
            ),
            redirect_stdout(output),
        ):
            exit_code = main(
                [
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
                ]
            )

        self.assertEqual(exit_code, 0)
        resolve.assert_called_once_with(
            "java/spring-boot",
            "ping-api",
            "jvm-java25",
            PROJECT_ROOT,
            load_profile="none",
            environment_profile="local-docker-compose",
            measurement_protocol="development-service",
            build_profile="local-gradle-docker",
        )

    def test_run_accepts_profile_flags_without_a_variant(self):
        config = object()
        output = io.StringIO()
        with (
            patch("pathlib.Path.cwd", return_value=PROJECT_ROOT),
            patch("hrw_runner.__main__.resolve_run_config", return_value=config) as resolve,
            patch(
                "hrw_runner.__main__.run_benchmark",
                return_value=PROJECT_ROOT / "results/run",
            ),
            redirect_stdout(output),
        ):
            exit_code = main(
                ["java/spring-boot", "ping-api", "--load-profile", "none"]
            )

        self.assertEqual(exit_code, 0)
        self.assertIsNone(resolve.call_args.args[2])
        self.assertEqual(resolve.call_args.kwargs["load_profile"], "none")

    def test_run_rejects_unknown_missing_and_duplicate_flags_with_usage(self):
        invalid_arguments = (
            ["java/spring-boot", "ping-api", "--unknown", "value"],
            ["java/spring-boot", "ping-api", "--load-profile"],
            [
                "java/spring-boot",
                "ping-api",
                "--load-profile",
                "none",
                "--load-profile",
                "none",
            ],
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                errors = io.StringIO()
                with (
                    patch("pathlib.Path.cwd", return_value=PROJECT_ROOT),
                    patch("hrw_runner.__main__.resolve_run_config") as resolve,
                    redirect_stderr(errors),
                ):
                    exit_code = main(arguments)

                self.assertEqual(exit_code, 2)
                self.assertIn("Usage: python -m hrw_runner", errors.getvalue())
                resolve.assert_not_called()

    def _write_result(self, root_dir: Path) -> None:
        result_path = root_dir / "results/java/spring-boot/jvm-java25/ping-api/run/result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            """
{
  "run_id": "2026-07-05T10-00-00_java_spring-boot_jvm-java25_ping-api",
  "scenario": "ping-api",
  "implementation": "java/spring-boot",
  "variant": "jvm-java25",
  "startup": {"ready_ms": 1234},
  "runtime_metrics": {"rps": 1000.25, "p95_ms": 2.5}
}
""".lstrip()
        )

    def _write_old_result(self, root_dir: Path) -> None:
        result_path = root_dir / "results/java/spring-boot/jvm-java25/ping-api/old/result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            """
{
  "run_id": "2026-07-04T10-00-00_java_spring-boot_jvm-java25_ping-api",
  "scenario": "ping-api",
  "implementation": "java/spring-boot",
  "variant": "jvm-java25",
  "startup": {"ready_ms": 2234},
  "runtime_metrics": {"rps": 500.25, "p95_ms": 4.5}
}
""".lstrip()
        )


if __name__ == "__main__":
    unittest.main()
