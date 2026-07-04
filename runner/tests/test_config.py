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
        self.assertEqual(config.load["warmup_duration"], "10s")
        self.assertEqual(config.load["test_duration"], "30s")

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


if __name__ == "__main__":
    unittest.main()
