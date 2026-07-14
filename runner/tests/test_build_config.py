import unittest
from pathlib import Path

from hrw_runner.build_config import resolve_build_run_config


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
