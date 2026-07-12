import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from hrw_runner.config import resolve_run_config
from hrw_runner.contracts import ContractValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ResolveRunConfigTest(unittest.TestCase):
    def test_resolves_spring_boot_alias_to_java_spring_boot(self):
        config = resolve_run_config("spring-boot", "ping-api", None)

        self.assertEqual(config.implementation, "java/spring-boot")
        self.assertEqual(config.language, "java")
        self.assertEqual(config.framework, "spring-boot")
        self.assertEqual(config.variant, "jvm-java25")
        self.assertEqual(config.runtime["java_version"], "25")
        self.assertEqual(config.runtime["spring_boot_version"], "4.1.0")
        self.assertEqual(config.load["vus"], 50)
        self.assertEqual(config.target["endpoint"], "/ping")
        self.assertNotIn("health_path", config.target)

    def test_resolves_contract_ownership_and_references(self):
        config = resolve_run_config("java/spring-boot", "ping-api", None)

        self.assertEqual(config.implementation_config["default_variant"], "jvm-java25")
        self.assertEqual(
            config.implementation_config["default_build_profile"],
            "local-gradle-docker",
        )
        self.assertEqual(config.environment_profile_config["id"], "local-docker-compose")
        self.assertEqual(config.measurement_protocol_config["id"], "development-service")
        self.assertEqual(config.load_profile_config["id"], "development-local")
        self.assertEqual(config.build_profile_config["id"], "local-gradle-docker")
        self.assertEqual(
            set(config.scenario_config["default_profiles"]),
            {"environment_profile", "measurement_protocol", "load_profile"},
        )
        self.assertNotIn("contracts", config.scenario_config)
        self.assertNotIn("implementation", config.scenario_config)
        self.assertNotIn("variant", config.scenario_config)
        self.assertNotIn("runtime", config.scenario_config)

    def test_profile_overrides_replace_all_owned_defaults(self):
        root_dir = self._copy_runnable_contracts()
        self._copy_profile(
            root_dir,
            "load-profiles/development-local.yaml",
            "load-profiles/alternate-load.yaml",
            "alternate-load",
        )
        self._copy_profile(
            root_dir,
            "environment-profiles/local-docker-compose.yaml",
            "environment-profiles/alternate-environment.yaml",
            "alternate-environment",
        )
        self._copy_profile(
            root_dir,
            "measurement-protocols/development-service.yaml",
            "measurement-protocols/alternate-measurement.yaml",
            "alternate-measurement",
        )
        self._copy_profile(
            root_dir,
            "build-profiles/local-gradle-docker.yaml",
            "build-profiles/alternate-build.yaml",
            "alternate-build",
        )

        config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            None,
            root_dir,
            load_profile="alternate-load",
            environment_profile="alternate-environment",
            measurement_protocol="alternate-measurement",
            build_profile="alternate-build",
        )

        self.assertEqual(config.load_profile_config["id"], "alternate-load")
        self.assertEqual(
            config.environment_profile_config["id"], "alternate-environment"
        )
        self.assertEqual(
            config.measurement_protocol_config["id"], "alternate-measurement"
        )
        self.assertEqual(config.build_profile_config["id"], "alternate-build")

    def test_disabled_load_profile_disables_scenario_load(self):
        config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            None,
            PROJECT_ROOT,
            load_profile="none",
        )

        self.assertEqual(config.load_profile_config["id"], "none")
        self.assertFalse(config.load["enabled"])

    def test_rejects_closed_load_profile_for_load_disabled_scenario(self):
        with self.assertRaisesRegex(
            ValueError,
            "Unsupported load profile 'development-local' semantics",
        ):
            resolve_run_config(
                "java/spring-boot",
                "cold-start-api",
                None,
                PROJECT_ROOT,
                load_profile="development-local",
            )

    def test_measurement_protocol_trials_own_lifecycle_startup_iterations(self):
        root_dir = self._copy_runnable_contracts()
        self._copy_scenario(root_dir, "cold-start-api")
        scenario_path = root_dir / "scenarios/cold-start-api/scenario.yaml"
        scenario = yaml.safe_load(scenario_path.read_text())
        scenario["startup"]["iterations"] = 2
        scenario_path.write_text(yaml.safe_dump(scenario, sort_keys=False))

        config = resolve_run_config(
            "java/spring-boot",
            "cold-start-api",
            None,
            root_dir,
        )

        self.assertEqual(config.measurement_protocol_config["trials"], 5)
        self.assertEqual(config.startup["iterations"], 5)

    def test_rejects_incompatible_measurement_evidence_family(self):
        with self.assertRaisesRegex(
            ValueError,
            "Incompatible measurement protocol 'cold-start' for smoke scenario "
            "'ping-api': expected service evidence, got lifecycle",
        ):
            resolve_run_config(
                "java/spring-boot",
                "ping-api",
                None,
                PROJECT_ROOT,
                measurement_protocol="cold-start",
            )

    def test_rejects_unsupported_executable_profile_semantics(self):
        root_dir = self._copy_runnable_contracts()
        cases = (
            (
                "load-profiles/development-local.yaml",
                "load-profiles/unsupported-load.yaml",
                "unsupported-load",
                "load_profile",
                "load profile",
                {
                    "model": "open",
                    "executor": "constant-arrival-rate",
                    "timing": {"warmup_seconds": 10, "measured_seconds": 20},
                    "phases": [{"duration_seconds": 20, "multiplier": 1.0}],
                },
            ),
            (
                "environment-profiles/local-docker-compose.yaml",
                "environment-profiles/unsupported-environment.yaml",
                "unsupported-environment",
                "environment_profile",
                "environment profile",
                {"orchestrator": "kubernetes", "load_generator": "separate-host"},
            ),
            (
                "measurement-protocols/development-service.yaml",
                "measurement-protocols/unsupported-measurement.yaml",
                "unsupported-measurement",
                "measurement_protocol",
                "measurement protocol",
                {
                    "timing_source": "profile",
                    "warmup_seconds": 10,
                    "measured_seconds": 20,
                },
            ),
            (
                "measurement-protocols/development-service.yaml",
                "measurement-protocols/unsupported-service-trials.yaml",
                "unsupported-service-trials",
                "measurement_protocol",
                "measurement protocol",
                {"trials": 2},
            ),
            (
                "build-profiles/local-gradle-docker.yaml",
                "build-profiles/unsupported-build.yaml",
                "unsupported-build",
                "build_profile",
                "build profile",
                {"dependency_cache": "empty"},
            ),
        )
        for source, destination, profile_id, argument, profile_type, changes in cases:
            with self.subTest(profile_type=profile_type, profile_id=profile_id):
                self._copy_profile(
                    root_dir,
                    source,
                    destination,
                    profile_id,
                    changes=changes,
                )

                with self.assertRaisesRegex(
                    ValueError,
                    f"Unsupported {profile_type} '{profile_id}' semantics",
                ):
                    resolve_run_config(
                        "java/spring-boot",
                        "ping-api",
                        None,
                        root_dir,
                        **{argument: profile_id},
                    )

    def test_validates_the_whole_repository_before_resolving(self):
        root_dir = self._copy_runnable_contracts()
        scenario = yaml.safe_load(
            (root_dir / "scenarios/ping-api/scenario.yaml").read_text()
        )
        invalid_path = root_dir / "scenarios/unrelated/scenario.yaml"
        invalid_path.parent.mkdir(parents=True)
        invalid_path.write_text(yaml.safe_dump(scenario, sort_keys=False))

        with self.assertRaises(ContractValidationError) as context:
            resolve_run_config("java/spring-boot", "ping-api", None, root_dir)

        self.assertIn("scenarios/unrelated/scenario.yaml", str(context.exception))
        self.assertIn("duplicate scenario identity", str(context.exception))

    def test_rejects_a_selected_contract_id_that_does_not_match_its_path(self):
        root_dir = self._copy_runnable_contracts()
        scenario_path = root_dir / "scenarios/ping-api/scenario.yaml"
        scenario = yaml.safe_load(scenario_path.read_text())
        scenario["id"] = "different-scenario"
        scenario_path.write_text(yaml.safe_dump(scenario, sort_keys=False))

        with self.assertRaises(ContractValidationError) as context:
            resolve_run_config("java/spring-boot", "ping-api", None, root_dir)

        self.assertIn("scenarios/ping-api/scenario.yaml", str(context.exception))
        self.assertIn("must match id 'different-scenario'", str(context.exception))

    def test_rejects_a_traversal_variant_outside_the_validated_catalog(self):
        root_dir = self._copy_runnable_contracts()
        app_dir = root_dir / "implementations/java/spring-boot"
        rogue_variant = yaml.safe_load(
            (app_dir / "variants/jvm-java25.yaml").read_text()
        )
        rogue_variant["id"] = "rogue"
        (app_dir / "rogue.yaml").write_text(
            yaml.safe_dump(rogue_variant, sort_keys=False)
        )

        with self.assertRaisesRegex(
            ValueError,
            "Unsupported variant for java/spring-boot: ../rogue",
        ):
            resolve_run_config(
                "java/spring-boot",
                "ping-api",
                "../rogue",
                root_dir,
            )

    def test_resolves_a_second_valid_implementation_from_the_catalog(self):
        root_dir = self._copy_runnable_contracts()
        app_dir = root_dir / "implementations/java/other"
        self._write_yaml(
            app_dir / "implementation.yaml",
            {
                "schema_version": "1.0",
                "id": "java/other",
                "contract_version": "1.0",
                "description": "Second Java implementation.",
                "language": "java",
                "framework": "other",
                "programming_model": "synchronous",
                "default_variant": "other-java25",
                "default_build_profile": "local-gradle-docker",
            },
        )
        self._write_yaml(
            app_dir / "variants/other-java25.yaml",
            {
                "schema_version": "1.0",
                "id": "other-java25",
                "contract_version": "1.0",
                "description": "Other Java 25 variant.",
                "implementation": "java/other",
                "runtime": {
                    "language": "java",
                    "java_version": "25",
                    "framework": "other",
                    "build_mode": "jvm",
                },
                "docker": {"image_tag": "hello-realworld/java-other:local"},
            },
        )

        config = resolve_run_config("java/other", "ping-api", None, root_dir)

        self.assertEqual(config.implementation, "java/other")
        self.assertEqual(config.language, "java")
        self.assertEqual(config.framework, "other")
        self.assertEqual(config.variant, "other-java25")
        self.assertEqual(config.app_dir, app_dir)
        self.assertEqual(config.scenario_dir, root_dir / "scenarios/ping-api")
        self.assertEqual(
            config.variant_file,
            app_dir / "variants/other-java25.yaml",
        )
        self.assertEqual(
            config.result_prefix,
            ("java", "other", "other-java25", "ping-api"),
        )

    def test_rejects_any_selected_draft_profile(self):
        root_dir = self._copy_runnable_contracts()
        cases = (
            (
                "load-profiles/development-local.yaml",
                "load-profiles/draft-load.yaml",
                "draft-load",
                "load_profile",
                "load profile",
            ),
            (
                "environment-profiles/local-docker-compose.yaml",
                "environment-profiles/draft-environment.yaml",
                "draft-environment",
                "environment_profile",
                "environment profile",
            ),
            (
                "measurement-protocols/development-service.yaml",
                "measurement-protocols/draft-measurement.yaml",
                "draft-measurement",
                "measurement_protocol",
                "measurement protocol",
            ),
            (
                "build-profiles/local-gradle-docker.yaml",
                "build-profiles/draft-build.yaml",
                "draft-build",
                "build_profile",
                "build profile",
            ),
        )
        for source, destination, profile_id, argument, profile_type in cases:
            with self.subTest(profile_type=profile_type):
                self._copy_profile(
                    root_dir,
                    source,
                    destination,
                    profile_id,
                    status="draft",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    f"Draft {profile_type} '{profile_id}' is not executable",
                ):
                    resolve_run_config(
                        "java/spring-boot",
                        "ping-api",
                        None,
                        root_dir,
                        **{argument: profile_id},
                    )

    def test_uses_canonical_result_prefix(self):
        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25")

        self.assertEqual(
            config.result_prefix,
            ("java", "spring-boot", "jvm-java25", "ping-api"),
        )

    def test_reads_configuration_from_yaml_files(self):
        root_dir = PROJECT_ROOT

        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25", root_dir)

        self.assertEqual(config.image_tag, "hello-realworld/java-spring-boot-jvm-java25:local")
        self.assertFalse(config.runtime["native_image"])
        self.assertFalse(config.runtime["virtual_threads"])
        self.assertEqual(config.load["warmup_duration"], "10s")
        self.assertEqual(config.load["test_duration"], "30s")

    def test_reads_virtual_thread_variant_configuration(self):
        root_dir = PROJECT_ROOT

        config = resolve_run_config(
            "java/spring-boot",
            "io-aggregation-api",
            "jvm-java25-virtual-threads",
            root_dir,
        )

        self.assertEqual(config.variant, "jvm-java25-virtual-threads")
        self.assertTrue(config.runtime["virtual_threads"])
        self.assertEqual(
            config.image_tag,
            "hello-realworld/java-spring-boot-jvm-java25-virtual-threads:local",
        )

    def test_reads_cold_start_configuration(self):
        root_dir = PROJECT_ROOT

        config = resolve_run_config(
            "java/spring-boot",
            "cold-start-api",
            "jvm-java25",
            root_dir,
        )

        self.assertFalse(config.load["enabled"])
        self.assertEqual(config.startup["iterations"], 5)
        self.assertEqual(config.startup["poll_interval_seconds"], 0.25)
        self.assertEqual(config.target["endpoint"], "/ping")
        self.assertNotIn("health_path", config.target)
        self.assertEqual(config.measurement_protocol_config["id"], "cold-start")
        self.assertEqual(config.load_profile_config["id"], "none")

    def test_reads_transactional_command_configuration(self):
        root_dir = PROJECT_ROOT

        config = resolve_run_config(
            "java/spring-boot",
            "transactional-command-api",
            "jvm-java25",
            root_dir,
        )

        self.assertEqual(config.target["startup_path"], "/ping")
        self.assertEqual(config.target["endpoint"], "/orders")
        self.assertTrue(config.scenario_config["services"]["postgres"])
        self.assertEqual(config.load["vus"], 25)

    def test_reads_io_aggregation_configuration(self):
        root_dir = PROJECT_ROOT

        config = resolve_run_config(
            "java/spring-boot",
            "io-aggregation-api",
            "jvm-java25",
            root_dir,
        )

        self.assertEqual(config.target["startup_path"], "/ping")
        self.assertEqual(config.target["endpoint"], "/aggregate")
        self.assertTrue(config.scenario_config["services"]["mock_upstream"])
        self.assertEqual(config.load["vus"], 25)

    def test_reads_io_aggregation_timeout_configuration(self):
        root_dir = PROJECT_ROOT

        config = resolve_run_config(
            "java/spring-boot",
            "io-aggregation-timeout-api",
            "jvm-java25",
            root_dir,
        )

        self.assertEqual(config.target["startup_path"], "/ping")
        self.assertEqual(config.target["endpoint"], "/aggregate")
        self.assertTrue(config.scenario_config["services"]["mock_upstream"])
        self.assertEqual(config.load["vus"], 5)

    def _copy_runnable_contracts(self) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root_dir = Path(temp_dir.name)
        shutil.copytree(PROJECT_ROOT / "contracts", root_dir / "contracts")

        source_app = PROJECT_ROOT / "implementations/java/spring-boot"
        app_dir = root_dir / "implementations/java/spring-boot"
        app_dir.mkdir(parents=True)
        shutil.copy2(source_app / "implementation.yaml", app_dir / "implementation.yaml")
        shutil.copytree(source_app / "variants", app_dir / "variants")

        self._copy_scenario(root_dir, "ping-api")
        return root_dir

    def _copy_scenario(self, root_dir: Path, scenario_id: str) -> None:
        scenario_dir = root_dir / "scenarios" / scenario_id
        scenario_dir.mkdir(parents=True)
        shutil.copy2(
            PROJECT_ROOT / "scenarios" / scenario_id / "scenario.yaml",
            scenario_dir / "scenario.yaml",
        )

    def _copy_profile(
        self,
        root_dir: Path,
        source: str,
        destination: str,
        profile_id: str,
        status: str | None = None,
        changes: dict[str, object] | None = None,
    ) -> None:
        value = yaml.safe_load((root_dir / "contracts" / source).read_text())
        value["id"] = profile_id
        if status is not None:
            value["status"] = status
        if changes is not None:
            value.update(changes)
        (root_dir / "contracts" / destination).write_text(
            yaml.safe_dump(value, sort_keys=False)
        )

    def _write_yaml(self, path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False))


if __name__ == "__main__":
    unittest.main()
