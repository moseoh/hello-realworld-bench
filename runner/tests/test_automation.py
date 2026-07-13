import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class WorkflowTrustBoundaryTest(unittest.TestCase):
    def test_official_benchmark_never_runs_for_pull_requests(self):
        workflow = self._load("official-benchmark.yml")

        self.assertEqual(set(workflow["on"]), {"workflow_call"})
        benchmark = workflow["jobs"]["benchmark"]
        self.assertEqual(
            benchmark["runs-on"], ["self-hosted", "linux", "x64", "hrw-home-k3s"]
        )
        self.assertEqual(benchmark["permissions"]["contents"], "read")
        self.assertEqual(benchmark["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["publish"]["permissions"], {"contents": "read"})
        publish_checkout = next(
            step
            for step in workflow["jobs"]["publish"]["steps"]
            if step.get("uses") == "actions/checkout@v4"
        )
        self.assertEqual(
            publish_checkout["with"]["token"],
            "${{ secrets.PUBLIC_REPO_TOKEN }}",
        )
        benchmark_step = next(
            step
            for step in benchmark["steps"]
            if step.get("name") == "Run official qualification set"
        )
        self.assertEqual(benchmark_step["working-directory"], "source")
        self.assertEqual(
            benchmark_step["env"]["IMPLEMENTATION"],
            "${{ steps.allowlist.outputs.implementation }}",
        )
        self.assertEqual(
            benchmark_step["env"]["VARIANT"],
            "${{ steps.allowlist.outputs.variant }}",
        )
        self.assertEqual(
            benchmark_step["env"]["HRW_TARGET_IMAGE"],
            "${{ steps.image_ref.outputs.target_image }}",
        )
        self.assertIn('"$IMPLEMENTATION" "$SCENARIO" "$VARIANT"', benchmark_step["run"])
        self.assertEqual(benchmark["strategy"]["max-parallel"], "1")
        self.assertEqual(
            workflow["jobs"]["publish"]["strategy"]["max-parallel"], "1"
        )

    def test_official_build_matrix_is_static_and_builds_both_implementations(self):
        workflow = self._load("official-benchmark.yml")
        build = workflow["jobs"]["build"]

        self.assertEqual(
            build["strategy"]["matrix"]["include"],
            [
                {
                    "implementation": "java/spring-boot",
                    "app_dir": "implementations/java/spring-boot",
                    "repository": "ghcr.io/moseoh/hello-realworld-bench/spring-boot",
                    "image_key": "spring-boot",
                },
                {
                    "implementation": "java/quarkus",
                    "app_dir": "implementations/java/quarkus",
                    "repository": "ghcr.io/moseoh/hello-realworld-bench/quarkus",
                    "image_key": "quarkus",
                },
            ],
        )
        self.assertNotIn("app_dir", workflow["on"]["workflow_call"]["inputs"])
        build_step = next(
            step
            for step in build["steps"]
            if step.get("name") == "Build target application"
        )
        self.assertEqual(build_step["working-directory"], "${{ matrix.app_dir }}")
        self.assertEqual(build_step["run"], "./gradlew clean build --no-daemon")

        ref_step = next(
            step
            for step in build["steps"]
            if step.get("name") == "Record immutable image reference"
        )
        self.assertIn("${{ steps.image.outputs.digest }}", ref_step["env"].values())
        self.assertIn("%s@%s", ref_step["run"])
        upload = next(
            step
            for step in build["steps"]
            if step.get("uses") == "actions/upload-artifact@v4"
        )
        self.assertIn("${{ matrix.image_key }}", upload["with"]["name"])
        self.assertIn("target-image.oci.tar", upload["with"]["path"])
        self.assertIn("image-ref.txt", upload["with"]["path"])

    def test_official_matrix_validation_rejects_canonical_duplicate_cells(self):
        workflow = self._load("official-benchmark.yml")
        validation = workflow["jobs"]["validate-matrix"]
        script = next(
            step["run"]
            for step in validation["steps"]
            if step.get("name") == "Validate benchmark matrix"
        )
        duplicate = [
            {
                "scenario": "ping-api",
                "load_profile": "platform-qualification-v1",
            },
            {
                "implementation": "java/spring-boot",
                "variant": "jvm-java25",
                "image_key": "spring-boot",
                "scenario": "ping-api",
                "load_profile": "platform-qualification-v1",
            },
        ]
        unique = [
            duplicate[0],
            {
                "implementation": "java/quarkus",
                "variant": "jvm-java25",
                "image_key": "quarkus",
                "scenario": "ping-api",
                "load_profile": "platform-qualification-v1",
            },
        ]

        self.assertNotEqual(self._run_matrix_validation(script, duplicate).returncode, 0)
        accepted = self._run_matrix_validation(script, unique)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("validate-matrix", workflow["jobs"]["build"]["needs"])
        self.assertIn("validate-matrix", workflow["jobs"]["benchmark"]["needs"])
        self.assertIn("validate-matrix", workflow["jobs"]["publish"]["needs"])

    def test_benchmark_matrix_is_allowlisted_before_use(self):
        workflow = self._load("official-benchmark.yml")
        benchmark = workflow["jobs"]["benchmark"]
        steps = benchmark["steps"]
        allowlist_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Allowlist benchmark cell"
        )
        allowlist = steps[allowlist_index]

        self.assertEqual(allowlist["id"], "allowlist")
        self.assertEqual(
            allowlist["env"],
            {
                "MATRIX_IMPLEMENTATION": "${{ matrix.implementation }}",
                "MATRIX_VARIANT": "${{ matrix.variant }}",
                "MATRIX_IMAGE_KEY": "${{ matrix.image_key }}",
                "MATRIX_SCENARIO": "${{ matrix.scenario }}",
                "MATRIX_LOAD_PROFILE": "${{ matrix.load_profile }}",
            },
        )
        script = allowlist["run"]
        self.assertIn("${MATRIX_IMPLEMENTATION:-java/spring-boot}", script)
        self.assertIn("${MATRIX_VARIANT:-jvm-java25}", script)
        self.assertIn("${MATRIX_IMAGE_KEY:-spring-boot}", script)
        self.assertIn("java/spring-boot:jvm-java25:spring-boot", script)
        self.assertIn("java/quarkus:jvm-java25:quarkus", script)
        for allowed_cell in (
            "ping-api:platform-qualification-v1",
            "transactional-command-api:steady",
            "transactional-command-api:capacity-ramp",
            "transactional-command-api:burst-recovery",
            "io-aggregation-api:steady",
            "io-aggregation-api:capacity-ramp",
            "io-aggregation-api:burst-recovery",
        ):
            self.assertIn(allowed_cell, script)

        for step in steps[:allowlist_index]:
            self.assertNotIn("${{ matrix.", step.get("run", ""))
        for step in steps[allowlist_index + 1 :]:
            self.assertNotIn("${{ matrix.", step.get("run", ""))

        download = next(
            step
            for step in steps
            if step.get("uses") == "actions/download-artifact@v4"
        )
        self.assertIn("${{ steps.allowlist.outputs.image_key }}", download["with"]["name"])

        verify_image = next(
            step
            for step in steps
            if step.get("name") == "Read immutable image reference"
        )
        self.assertIn("image-ref.txt", verify_image["run"])
        self.assertIn("@sha256:", verify_image["run"])

        upload = next(
            step
            for step in steps
            if step.get("uses") == "actions/upload-artifact@v4"
        )
        self.assertIn("${{ steps.allowlist.outputs.image_key }}", upload["with"]["name"])

    def test_benchmark_allowlist_accepts_only_known_cells(self):
        workflow = self._load("official-benchmark.yml")
        script = next(
            step
            for step in workflow["jobs"]["benchmark"]["steps"]
            if step.get("name") == "Allowlist benchmark cell"
        )["run"]

        spring = self._run_allowlist(
            script,
            scenario="ping-api",
            load_profile="platform-qualification-v1",
        )
        self.assertEqual(spring.returncode, 0, spring.stderr)
        self.assertIn("implementation=java/spring-boot", spring.stdout)
        self.assertIn("variant=jvm-java25", spring.stdout)
        self.assertIn("image_key=spring-boot", spring.stdout)

        quarkus = self._run_allowlist(
            script,
            implementation="java/quarkus",
            variant="jvm-java25",
            image_key="quarkus",
            scenario="transactional-command-api",
            load_profile="steady",
        )
        self.assertEqual(quarkus.returncode, 0, quarkus.stderr)

        mixed_tuple = self._run_allowlist(
            script,
            implementation="java/quarkus",
            variant="jvm-java25",
            image_key="spring-boot",
            scenario="transactional-command-api",
            load_profile="steady",
        )
        self.assertNotEqual(mixed_tuple.returncode, 0)

        unknown_cell = self._run_allowlist(
            script,
            scenario="io-aggregation-timeout-api",
            load_profile="steady",
        )
        self.assertNotEqual(unknown_cell.returncode, 0)

    def test_publish_downloads_the_matching_implementation_artifact(self):
        workflow = self._load("official-benchmark.yml")
        publish = workflow["jobs"]["publish"]
        steps = publish["steps"]
        allowlist = next(
            step for step in steps if step.get("name") == "Allowlist publish cell"
        )
        self.assertIn("java/spring-boot:jvm-java25:spring-boot", allowlist["run"])
        self.assertIn("java/quarkus:jvm-java25:quarkus", allowlist["run"])
        download = next(
            step
            for step in steps
            if step.get("uses") == "actions/download-artifact@v4"
        )
        self.assertIn("${{ steps.allowlist.outputs.image_key }}", download["with"]["name"])

    def test_pull_request_ci_uses_only_github_hosted_runner(self):
        workflow = self._load("ci.yml")

        self.assertIn("pull_request", workflow["on"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["check"]["runs-on"], "ubuntu-latest")
        uses = [step.get("uses") for step in workflow["jobs"]["check"]["steps"]]
        self.assertIn("grafana/setup-k6-action@v1", uses)
        setup_k6 = next(
            step
            for step in workflow["jobs"]["check"]["steps"]
            if step.get("uses") == "grafana/setup-k6-action@v1"
        )
        self.assertEqual(setup_k6["with"]["k6-version"], "2.1.0")
        setup_java = next(
            step
            for step in workflow["jobs"]["check"]["steps"]
            if step.get("uses") == "actions/setup-java@v4"
        )
        self.assertEqual(setup_java["with"]["java-version"], "25")
        implementation_tests = next(
            step
            for step in workflow["jobs"]["check"]["steps"]
            if step.get("name") == "Test implementations"
        )
        self.assertEqual(implementation_tests["run"], "make test-spring test-quarkus")

    def _load(self, name: str):
        with (ROOT / ".github/workflows" / name).open() as file:
            return yaml.load(file, Loader=yaml.BaseLoader)

    def _run_allowlist(
        self,
        script: str,
        *,
        implementation: str = "",
        variant: str = "",
        image_key: str = "",
        scenario: str,
        load_profile: str,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.NamedTemporaryFile() as output:
            env = {
                **os.environ,
                "GITHUB_OUTPUT": output.name,
                "MATRIX_IMPLEMENTATION": implementation,
                "MATRIX_VARIANT": variant,
                "MATRIX_IMAGE_KEY": image_key,
                "MATRIX_SCENARIO": scenario,
                "MATRIX_LOAD_PROFILE": load_profile,
            }
            completed = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", script],
                check=False,
                capture_output=True,
                env=env,
                text=True,
            )
            output.seek(0)
            completed.stdout = output.read().decode()
            return completed

    def _run_matrix_validation(
        self, script: str, matrix: list[dict[str, str]]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            check=False,
            capture_output=True,
            env={**os.environ, "MATRIX_JSON": json.dumps(matrix)},
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
