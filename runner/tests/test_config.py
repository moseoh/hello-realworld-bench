import unittest
from pathlib import Path

from hrw_runner.config import resolve_run_config


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
        self.assertEqual(config.environment_profile_config["id"], "local-docker-compose")
        self.assertEqual(config.measurement_protocol_config["id"], "development-service")
        self.assertEqual(config.load_profile_config["id"], "development-local")
        self.assertEqual(config.build_profile_config["id"], "local-gradle-docker")
        self.assertNotIn("implementation", config.scenario_config)
        self.assertNotIn("variant", config.scenario_config)
        self.assertNotIn("runtime", config.scenario_config)

    def test_uses_canonical_result_prefix(self):
        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25")

        self.assertEqual(
            config.result_prefix,
            ("java", "spring-boot", "jvm-java25", "ping-api"),
        )

    def test_reads_configuration_from_yaml_files(self):
        root_dir = Path(__file__).resolve().parents[2]

        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25", root_dir)

        self.assertEqual(config.image_tag, "hello-realworld/java-spring-boot-jvm-java25:local")
        self.assertFalse(config.runtime["native_image"])
        self.assertFalse(config.runtime["virtual_threads"])
        self.assertEqual(config.load["warmup_duration"], "10s")
        self.assertEqual(config.load["test_duration"], "30s")

    def test_reads_virtual_thread_variant_configuration(self):
        root_dir = Path(__file__).resolve().parents[2]

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
        root_dir = Path(__file__).resolve().parents[2]

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
        root_dir = Path(__file__).resolve().parents[2]

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
        root_dir = Path(__file__).resolve().parents[2]

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
        root_dir = Path(__file__).resolve().parents[2]

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


if __name__ == "__main__":
    unittest.main()
