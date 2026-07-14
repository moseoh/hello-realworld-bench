import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from hrw_runner.build_config import resolve_build_run_config
from hrw_runner.contracts import ContractValidationError, read_contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ResolveBuildRunConfigTest(unittest.TestCase):
    def test_resolves_only_build_selection_contracts(self):
        config = resolve_build_run_config(
            "java/spring-boot",
            "jvm-java25",
            PROJECT_ROOT,
            environment_profile="home-build-v1",
            measurement_protocol="official-build-v1",
            build_profile="official-gradle-docker-v1",
        )

        self.assertEqual(config.implementation, "java/spring-boot")
        self.assertEqual(config.variant, "jvm-java25")
        self.assertEqual(config.app_dir, PROJECT_ROOT / "implementations/java/spring-boot")
        self.assertEqual(config.environment_profile_config["id"], "home-build-v1")
        self.assertEqual(config.measurement_protocol_config["id"], "official-build-v1")
        self.assertEqual(config.build_profile_config["id"], "official-gradle-docker-v1")
        self.assertEqual(
            set(config.selected_contracts),
            {
                "implementation",
                "variant",
                "environment_profile",
                "measurement_protocol",
                "build_profile",
            },
        )
        self.assertFalse(hasattr(config, "scenario"))
        self.assertFalse(hasattr(config, "load"))

    def test_build_contracts_pin_the_official_three_trial_home_machine_profile(self):
        environment = yaml.safe_load(
            (PROJECT_ROOT / "contracts/environment-profiles/home-build-v1.yaml").read_text()
        )
        protocol = yaml.safe_load(
            (PROJECT_ROOT / "contracts/measurement-protocols/official-build-v1.yaml").read_text()
        )
        profile = yaml.safe_load(
            (PROJECT_ROOT / "contracts/build-profiles/official-gradle-docker-v1.yaml").read_text()
        )

        self.assertEqual(
            environment["build"],
            {
                "runner_labels": ["self-hosted", "linux", "x64", "hrw-home-k3s"],
                "platform": "linux/amd64",
                "machine_id": "f66cd2d134b94bb18eb7e531d1baf343",
                "cpu_model": "AMD Ryzen 7 5825U",
                "min_logical_cpus": 16,
                "min_memory_bytes": 29313151795,
                "container_engine": "docker",
                "daemon_mode": "rootless",
                "runner_uid": 1000,
                "docker_version": "29.6.1",
                "buildx_version": "0.35.0",
            },
        )
        self.assertEqual(protocol["trials"], 3)
        self.assertEqual(
            protocol["build"],
            {
                "start_boundary": "operation-command-start",
                "completion_boundary": "operation-command-exit",
            },
        )
        self.assertEqual(
            profile["build"]["operations"],
            [
                "gradle_clean_build",
                "image_package",
                "gradle_incremental_rebuild",
                "image_rebuild",
            ],
        )
        self.assertEqual(profile["dependency_cache"], "immutable-fresh-copy-seed")
        self.assertEqual(
            profile["build"]["image_cache"],
            {"image_package": "base-only", "image_rebuild": "first-package"},
        )
        self.assertEqual(profile["build"]["workspace"], "fresh-copy")
        self.assertEqual(profile["build"]["source_probe"], "0->1")
        self.assertEqual(
            profile["build"]["java_executor"],
            {
                "image": "eclipse-temurin:25-jdk@sha256:68868d04fa9cfd5f5c6abec0b5cef86d8de2bf9c62c37c7d3e4f0f80f5cfd7ff",
                "cpu": "2",
                "memory": "4GiB",
                "extra_swap": "none",
                "user": "0:0",
            },
        )
        self.assertEqual(
            profile["build"]["buildkit"],
            {
                "image": "moby/buildkit:buildx-stable-1@sha256:0168606be2315b7c807a03b3d8aa79beefdb31c98740cebdffdfeebf31190c9f",
                "builder": "per-trial-fresh",
                "cpu_quota": 200000,
                "cpu_period": 100000,
                "memory": "4g",
                "memory_swap": "4g",
            },
        )

    def test_build_measurement_protocol_rejects_lifecycle_configuration(self):
        root = self._temporary_contract_root()
        value = yaml.safe_load(
            (PROJECT_ROOT / "contracts/measurement-protocols/official-build-v1.yaml").read_text()
        )
        value["lifecycle"] = {
            "start_boundary": "image-entrypoint-pre-exec",
            "completion_boundary": "first-valid-response-complete",
            "observer_transport": "pod-localhost",
            "poll_interval_ms": 10,
            "request_timeout_ms": 250,
            "trial_timeout_seconds": 60,
            "between_trials_seconds": 5,
            "image_pull_policy": "Never",
        }
        path = root / "contracts/measurement-protocols/build.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False))

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "measurement-protocol", root)

        self.assertIn("lifecycle", str(context.exception))

    def test_java25_variants_pin_structured_offline_build_inputs(self):
        expected = {
            "clean_command": [
                "./gradlew",
                "clean",
                "build",
                "--offline",
                "--no-daemon",
                "--no-build-cache",
            ],
            "incremental_command": [
                "./gradlew",
                "build",
                "--offline",
                "--no-daemon",
                "--no-build-cache",
            ],
            "incremental_input": {
                "path": "src/main/java/org/hellorealworld/benchmark/BuildBenchmarkProbe.java",
                "from": "public static final int VALUE = 0;",
                "to": "public static final int VALUE = 1;",
            },
            "dockerfile": "Dockerfile",
            "context": ".",
        }

        for framework in ("spring-boot", "quarkus"):
            with self.subTest(framework=framework):
                framework_expected = {
                    **expected,
                    "application_artifact": {
                        "type": "glob" if framework == "spring-boot" else "directory",
                        "path": (
                            "build/libs/*.jar"
                            if framework == "spring-boot"
                            else "build/quarkus-app"
                        ),
                    },
                }
                variant = yaml.safe_load(
                    (
                        PROJECT_ROOT
                        / "implementations/java"
                        / framework
                        / "variants/jvm-java25.yaml"
                    ).read_text()
                )
                self.assertEqual(variant["build"], framework_expected)

    def test_non_host_build_official_profile_keeps_platform_requirements_with_build_block(self):
        root = self._temporary_contract_root()
        value = yaml.safe_load(
            (PROJECT_ROOT / "contracts/environment-profiles/home-k3s-v1.yaml").read_text()
        )
        for field in ("cluster", "resources", "images", "validity"):
            del value[field]
        value["build"] = yaml.safe_load(
            (PROJECT_ROOT / "contracts/environment-profiles/home-build-v1.yaml").read_text()
        )["build"]
        path = root / "contracts/environment-profiles/service.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False))

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "environment-profile", root)

        self.assertIn("cluster", str(context.exception))

    def test_host_build_environment_requires_build_block(self):
        root = self._temporary_contract_root()
        value = yaml.safe_load(
            (PROJECT_ROOT / "contracts/environment-profiles/home-k3s-v1.yaml").read_text()
        )
        value["orchestrator"] = "host-build"
        value["load_generator"] = "none"
        path = root / "contracts/environment-profiles/host-build.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False))

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "environment-profile", root)

        self.assertIn("build", str(context.exception))

    def test_service_and_lifecycle_environment_profiles_keep_platform_blocks(self):
        root = self._temporary_contract_root()

        for profile_id in ("home-k3s-v1", "home-k3s-lifecycle-v1"):
            with self.subTest(profile_id=profile_id):
                value = yaml.safe_load(
                    (
                        PROJECT_ROOT
                        / "contracts/environment-profiles"
                        / f"{profile_id}.yaml"
                    ).read_text()
                )
                del value["validity"]
                path = root / "contracts/environment-profiles" / f"{profile_id}.yaml"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(yaml.safe_dump(value, sort_keys=False))

                with self.assertRaises(ContractValidationError) as context:
                    read_contract(path, "environment-profile", root)

                self.assertIn("validity", str(context.exception))

    def _temporary_contract_root(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(
            PROJECT_ROOT / "contracts/schemas",
            root / "contracts/schemas",
        )
        return root

    def test_rejects_service_measurement_protocol_for_build_evidence(self):
        with self.assertRaisesRegex(ValueError, "measurement protocol"):
            resolve_build_run_config(
                "java/spring-boot",
                "jvm-java25",
                PROJECT_ROOT,
                environment_profile="home-build-v1",
                measurement_protocol="official-service-v1",
                build_profile="official-gradle-docker-v1",
            )

    def test_rejects_non_build_environment_profile(self):
        with self.assertRaisesRegex(ValueError, "environment profile"):
            resolve_build_run_config(
                "java/quarkus",
                "jvm-java25",
                PROJECT_ROOT,
                environment_profile="home-k3s-v1",
                measurement_protocol="official-build-v1",
                build_profile="official-gradle-docker-v1",
            )


if __name__ == "__main__":
    unittest.main()
