import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class WorkflowTrustBoundaryTest(unittest.TestCase):
    def test_official_build_workflow_has_only_frozen_build_inputs(self):
        workflow = self._load("official-build-benchmark.yml")
        inputs = workflow["on"]["workflow_call"]["inputs"]

        self.assertEqual(set(workflow["on"]), {"workflow_call"})
        self.assertEqual(set(inputs), {"source_sha", "implementation"})
        self.assertEqual(
            workflow["concurrency"],
            {"group": "official-home-k3s", "cancel-in-progress": "false"},
        )

    def test_official_build_workflow_keeps_measurement_untrusted_and_publication_hosted(self):
        workflow = self._load("official-build-benchmark.yml")
        benchmark = workflow["jobs"]["benchmark"]
        publish = workflow["jobs"]["publish"]

        self.assertEqual(
            benchmark["runs-on"], ["self-hosted", "linux", "x64", "hrw-home-k3s"]
        )
        self.assertEqual(benchmark["permissions"], {"contents": "read"})
        self.assertEqual(benchmark["strategy"]["max-parallel"], "1")
        self.assertNotIn("secrets", benchmark)
        self.assertEqual(publish["runs-on"], "ubuntu-latest")
        checkout = next(
            step for step in publish["steps"] if step.get("uses") == "actions/checkout@v4"
        )
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertNotIn("token", checkout["with"])
        publish_uv = [
            step
            for step in publish["steps"]
            if step.get("uses") == "astral-sh/setup-uv@v6"
        ]
        self.assertEqual(len(publish_uv), 1)
        self.assertNotEqual(publish_uv[0].get("with", {}).get("enable-cache"), "true")
        install = next(
            step for step in publish["steps"] if step.get("name") == "Install frozen publisher dependencies"
        )
        self.assertIn("uv sync --project runner --frozen --no-cache", install["run"])
        validate = next(
            step for step in publish["steps"] if step.get("name") == "Revalidate build evidence"
        )
        self.assertIn('git rev-parse "$SOURCE_SHA"', validate["run"])
        self.assertIn("create_deterministic_build_archive", validate["run"])
        self.assertIn("EXPECTED_IMPLEMENTATION", validate["env"])
        self.assertIn("EXPECTED_VARIANT", validate["env"])

    def test_official_build_workflow_recovers_exact_resources_and_uses_attempt_identity(self):
        workflow = self._load("official-build-benchmark.yml")
        benchmark = workflow["jobs"]["benchmark"]
        publish = workflow["jobs"]["publish"]
        recover = next(
            step for step in benchmark["steps"] if step.get("name") == "Recover interrupted build campaign"
        )
        cleanup = next(
            step for step in benchmark["steps"] if step.get("name") == "Cleanup build campaign resources"
        )
        self.assertEqual(cleanup["if"], "${{ always() }}")
        self.assertIn("build-cleanup", recover["run"])
        self.assertIn("build-cleanup", cleanup["run"])

        identity = next(
            step for step in benchmark["steps"] if step.get("name") == "Select raw artifact identity"
        )
        self.assertEqual(identity["env"]["RUN_ATTEMPT"], "${{ github.run_attempt }}")
        self.assertEqual(
            benchmark["outputs"]["raw_artifact_name"],
            "${{ steps.artifact.outputs.name }}",
        )
        download = next(
            step for step in publish["steps"] if step.get("uses") == "actions/download-artifact@v4"
        )
        self.assertEqual(download["with"]["name"], "${{ needs.benchmark.outputs.raw_artifact_name }}")

    def test_official_build_workflow_creates_archive_from_validated_allowlist(self):
        workflow = self._load("official-build-benchmark.yml")
        publish = workflow["jobs"]["publish"]
        validate = next(
            step for step in publish["steps"] if step.get("name") == "Revalidate build evidence"
        )
        prepare = next(
            step for step in publish["steps"] if step.get("name") == "Prepare raw evidence release"
        )
        self.assertIn("create_deterministic_build_archive", validate["run"])
        self.assertNotIn("tar -C raw-run-set -czf", prepare["run"])
        self.assertNotIn("PUBLIC_REPO_TOKEN", validate.get("env", {}))

    def test_official_build_workflow_requires_a_full_source_sha_before_checkout(self):
        workflow = self._load("official-build-benchmark.yml")

        for job_name in ("benchmark", "publish"):
            with self.subTest(job_name=job_name):
                validate = next(
                    step
                    for step in workflow["jobs"][job_name]["steps"]
                    if step.get("name") == "Validate source SHA"
                )
                self.assertIn("^[0-9a-f]{40}$", validate["run"])

    def test_raw_run_set_id_cannot_inject_workflow_output_or_environment_files(self):
        workflow = self._load("official-build-benchmark.yml")
        prepare = next(
            step
            for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Prepare raw evidence release"
        )
        self.assertNotIn("$GITHUB_ENV", prepare["run"])

        for unsafe_id in (
            "valid\nBASH_ENV=../raw-run-set/payload.sh",
            "valid\x01control",
        ):
            with self.subTest(unsafe_id=repr(unsafe_id)):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    raw = root / "raw-run-set"
                    raw.mkdir()
                    (raw / "build-run-set.json").write_text(
                        json.dumps({"run_set_id": unsafe_id})
                    )
                    github_output = root / "github-output"
                    github_env = root / "github-env"
                    github_output.touch()
                    github_env.touch()

                    completed = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", prepare["run"]],
                        cwd=root,
                        check=False,
                        capture_output=True,
                        text=True,
                        env={
                            **os.environ,
                            "GITHUB_OUTPUT": str(github_output),
                            "GITHUB_ENV": str(github_env),
                            "REPOSITORY": "moseoh/hello-realworld-bench",
                        },
                    )

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertEqual(github_output.read_text(), "")
                    self.assertEqual(github_env.read_text(), "")

    def test_official_build_workflow_allowlists_the_only_measurement_cells(self):
        workflow = self._load("official-build-benchmark.yml")
        allowlist = next(
            step
            for step in workflow["jobs"]["benchmark"]["steps"]
            if step.get("name") == "Allowlist build implementation"
        )
        self.assertIn("java/spring-boot:jvm-java25", allowlist["run"])
        self.assertIn("java/quarkus:jvm-java25", allowlist["run"])
        run_build_set = next(
            step
            for step in workflow["jobs"]["benchmark"]["steps"]
            if step.get("name") == "Run official build set"
        )
        self.assertIn("home-build-v1", run_build_set["run"])
        self.assertIn("official-build-v1", run_build_set["run"])
        self.assertIn("official-gradle-docker-v1", run_build_set["run"])

    def test_makefile_exposes_frozen_build_set_command(self):
        makefile = (ROOT / "Makefile").read_text()

        self.assertIn("build-set:", makefile)
        self.assertIn("python -m hrw_runner build-set $(IMPLEMENTATION)", makefile)
        self.assertIn("--environment-profile home-build-v1", makefile)
        self.assertIn("--measurement-protocol official-build-v1", makefile)
        self.assertIn("--build-profile official-gradle-docker-v1", makefile)

    def test_public_docs_match_the_official_build_contract_and_pending_smoke(self):
        automation = (ROOT / "docs/automation.md").read_text()
        methodology = (ROOT / "docs/methodology.md").read_text()
        evidence = (ROOT / "docs/evidence-model.md").read_text()
        manifest = (ROOT / "docs/resolved-run-manifest.md").read_text()

        for required in (
            "Docker Engine",
            "Docker Buildx",
            "build-run-sets/<cohort-fingerprint>/<run-set-id>/",
            "raw-build-evidence.tar.gz",
            "persist-credentials: false",
            "post-merge trusted-home smoke",
        ):
            self.assertIn(required, automation)
        for required in (
            "gradle_clean_build_ms",
            "gradle_incremental_rebuild_ms",
            "image_package_ms",
            "image_rebuild_ms",
            "exactly three",
            "runtime-base",
            "fresh copy",
        ):
            self.assertIn(required, methodology)
        for required in (
            "build-trial.json",
            "build-run-set.json",
            "build-resolved-manifest.json",
            "closed regular-file set",
        ):
            self.assertIn(required, evidence)
        self.assertIn(
            "environment, measurement, and build profile contracts",
            manifest,
        )
        self.assertIn("implementation and variant", manifest)

    def test_worker_separates_official_publication_from_calibration(self):
        workflow = self._load("official-benchmark.yml")
        inputs = workflow["on"]["workflow_call"]["inputs"]

        self.assertEqual(inputs["measurement_protocol"]["default"], "official-service-v1")
        self.assertEqual(inputs["publish_results"]["default"], "true")
        self.assertEqual(workflow["jobs"]["publish"]["if"], "${{ inputs.publish_results }}")

        validation = next(
            step
            for step in workflow["jobs"]["validate-matrix"]["steps"]
            if step.get("name") == "Validate execution mode"
        )
        self.assertIn("official-service-v1:true", validation["run"])
        self.assertIn("official-cold-start-v1:true", validation["run"])
        self.assertIn("calibration-service:false", validation["run"])

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
        self.assertEqual(
            benchmark_step["env"]["ENVIRONMENT_PROFILE"],
            "${{ steps.allowlist.outputs.environment_profile }}",
        )
        self.assertIn(
            '--environment-profile "$ENVIRONMENT_PROFILE"',
            benchmark_step["run"],
        )
        self.assertIn('"$IMPLEMENTATION" "$SCENARIO" "$VARIANT"', benchmark_step["run"])
        self.assertEqual(benchmark["strategy"]["max-parallel"], "1")
        self.assertEqual(
            workflow["jobs"]["publish"]["strategy"]["max-parallel"], "1"
        )
        self.assertIn("HRW_NAMESPACE_RECORD", benchmark_step["env"])
        recovery = next(
            step
            for step in benchmark["steps"]
            if step.get("name") == "Recover interrupted benchmark namespaces"
        )
        self.assertIn('"$RUNNER_TEMP"/hrw-*.namespace', recovery["run"])
        self.assertIn("app.kubernetes.io/part-of", recovery["run"])
        cleanup = next(
            step
            for step in benchmark["steps"]
            if step.get("name") == "Cleanup canceled benchmark namespace"
        )
        self.assertEqual(cleanup["if"], "${{ always() }}")
        self.assertIn("app.kubernetes.io/part-of", cleanup["run"])
        self.assertIn('delete namespace "$namespace"', cleanup["run"])

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

    def test_interrupted_namespace_recovery_deletes_only_recorded_benchmark(self):
        workflow = self._load("official-benchmark.yml")
        recovery = next(
            step
            for step in workflow["jobs"]["benchmark"]["steps"]
            if step.get("name") == "Recover interrupted benchmark namespaces"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            namespace = "hrw-20260713t010203-abcdef0"
            marker = root / "hrw-old.namespace"
            marker.write_text(f"{namespace}\n")
            delete_log = root / "deleted"
            executable = root / "kubectl"
            executable.write_text(
                "#!/bin/sh\n"
                "if [ \"$3\" = get ]; then\n"
                "  printf hello-realworld-bench\n"
                "elif [ \"$3\" = delete ]; then\n"
                "  printf '%s\\n' \"$5\" >> \"$DELETE_LOG\"\n"
                "fi\n"
            )
            executable.chmod(0o755)
            completed = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", recovery["run"]],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "RUNNER_TEMP": str(root),
                    "DELETE_LOG": str(delete_log),
                },
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(delete_log.read_text(), f"{namespace}\n")

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
                "MEASUREMENT_PROTOCOL": "${{ inputs.measurement_protocol }}",
                "PUBLISH_RESULTS": "${{ inputs.publish_results }}",
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
            "read-heavy-query-api:steady",
            "read-heavy-query-api:capacity-ramp",
            "read-heavy-query-api:burst-recovery",
            "read-heavy-query-api:calibration-steady:calibration-service",
            "read-heavy-query-api:calibration-burst:calibration-service",
            "cold-start-api:none:official-cold-start-v1",
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

        calibration = self._run_allowlist(
            script,
            implementation="java/quarkus",
            variant="jvm-java25",
            image_key="quarkus",
            scenario="read-heavy-query-api",
            load_profile="calibration-burst",
            measurement_protocol="calibration-service",
            publish_results="false",
        )
        self.assertEqual(calibration.returncode, 0, calibration.stderr)
        self.assertIn("measurement_protocol=calibration-service", calibration.stdout)
        self.assertIn("environment_profile=home-k3s-calibration", calibration.stdout)

        official = self._run_allowlist(
            script,
            scenario="transactional-command-api",
            load_profile="steady",
        )
        self.assertEqual(official.returncode, 0, official.stderr)
        self.assertIn("environment_profile=home-k3s-v1", official.stdout)

        lifecycle = self._run_allowlist(
            script,
            implementation="java/quarkus",
            variant="jvm-java25",
            image_key="quarkus",
            scenario="cold-start-api",
            load_profile="none",
            measurement_protocol="official-cold-start-v1",
        )
        self.assertEqual(lifecycle.returncode, 0, lifecycle.stderr)
        self.assertIn(
            "environment_profile=home-k3s-lifecycle-v1", lifecycle.stdout
        )

        published_calibration = self._run_allowlist(
            script,
            scenario="read-heavy-query-api",
            load_profile="calibration-steady",
            measurement_protocol="calibration-service",
            publish_results="true",
        )
        self.assertNotEqual(published_calibration.returncode, 0)

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
        measurement_protocol: str = "official-service-v1",
        publish_results: str = "true",
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
                "MEASUREMENT_PROTOCOL": measurement_protocol,
                "PUBLISH_RESULTS": publish_results,
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
