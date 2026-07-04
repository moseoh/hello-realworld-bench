import unittest

from hrw_runner.config import resolve_run_config


class ResolveRunConfigTest(unittest.TestCase):
    def test_resolves_spring_boot_alias_to_java_spring_boot(self):
        config = resolve_run_config("spring-boot", "ping-api", None)

        self.assertEqual(config.implementation, "java/spring-boot")
        self.assertEqual(config.language, "java")
        self.assertEqual(config.framework, "spring-boot")
        self.assertEqual(config.variant, "jvm-java25")

    def test_uses_canonical_result_prefix(self):
        config = resolve_run_config("java/spring-boot", "ping-api", "jvm-java25")

        self.assertEqual(
            config.result_prefix,
            ("java", "spring-boot", "jvm-java25", "ping-api"),
        )


if __name__ == "__main__":
    unittest.main()
