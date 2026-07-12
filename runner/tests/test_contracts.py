import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from hrw_runner.contracts import (
    ContractValidationError,
    canonical_contract_digest,
    read_contract,
    validate_repository_contracts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ContractValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.temp_dir.name)
        shutil.copytree(
            PROJECT_ROOT / "contracts" / "schemas",
            self.root_dir / "contracts" / "schemas",
        )
        self.create_repository()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_repository(self):
        self.write_yaml(
            "implementations/python/example/implementation.yaml",
            {
                "schema_version": "1.0",
                "id": "python/example",
                "contract_version": "1.0",
                "description": "Example implementation.",
                "language": "python",
                "framework": "example",
                "programming_model": "sync",
                "default_variant": "default",
            },
        )
        self.write_yaml(
            "implementations/python/example/variants/default.yaml",
            {
                "schema_version": "1.0",
                "id": "default",
                "contract_version": "1.0",
                "description": "Default variant.",
                "implementation": "python/example",
                "runtime": {
                    "language": "python",
                    "framework": "example",
                    "build_mode": "interpreted",
                },
                "docker": {"image_tag": "example:local"},
            },
        )
        self.write_yaml(
            "scenarios/example-scenario/scenario.yaml",
            {
                "schema_version": "1.0",
                "id": "example-scenario",
                "contract_version": "1.0",
                "description": "Example scenario.",
                "kind": "service",
                "question": "Does the example work?",
                "measures": ["response time"],
                "does_not_measure": ["throughput"],
                "dependencies": [],
                "variants": [{"id": "baseline", "description": "Baseline."}],
                "contracts": {
                    "environment_profile": "local",
                    "measurement_protocol": "service",
                    "load_profile": "development",
                    "build_profile": "build",
                },
                "target": {
                    "base_url": "http://localhost:8080",
                    "endpoint": "/example",
                },
                "services": {
                    "postgres": False,
                    "redis": False,
                    "kafka": False,
                    "mock_upstream": False,
                },
                "load": {"enabled": False},
                "metrics": {"collect": ["response_time"]},
            },
        )
        self.write_yaml(
            "contracts/load-profiles/development.yaml",
            {
                "schema_version": "1.0",
                "id": "development",
                "contract_version": "1.0",
                "description": "Development load profile.",
                "status": "development",
                "model": "closed",
                "executor": "constant-vus",
                "timing": {"source": "scenario"},
                "phases": [{"source": "scenario", "vus": None}],
            },
        )
        self.write_yaml(
            "contracts/environment-profiles/local.yaml",
            {
                "schema_version": "1.0",
                "id": "local",
                "contract_version": "1.0",
                "description": "Local environment.",
                "status": "development",
                "orchestrator": "docker-compose",
                "official": False,
                "load_generator": "same-host",
            },
        )
        self.write_yaml(
            "contracts/measurement-protocols/service.yaml",
            {
                "schema_version": "1.0",
                "id": "service",
                "contract_version": "1.0",
                "description": "Service measurements.",
                "status": "development",
                "evidence_family": "service",
                "trials": 1,
                "timing_source": "scenario",
                "warmup_seconds": None,
                "measured_seconds": None,
            },
        )
        self.write_yaml(
            "contracts/build-profiles/build.yaml",
            {
                "schema_version": "1.0",
                "id": "build",
                "contract_version": "1.0",
                "description": "Build profile.",
                "status": "development",
                "build_tool": "example",
                "dependency_cache": "persistent",
                "image_cache": "enabled",
                "image_input": "source",
            },
        )

    def write_yaml(self, relative_path, value):
        path = self.root_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False))

    def read_yaml(self, relative_path):
        return yaml.safe_load((self.root_dir / relative_path).read_text())

    def test_canonical_digest_ignores_mapping_order(self):
        self.assertEqual(
            canonical_contract_digest({"b": 2, "a": 1}),
            canonical_contract_digest({"a": 1, "b": 2}),
        )

    def test_read_contract_reports_schema_error_with_path_and_location(self):
        path = self.root_dir / "implementations/python/example/implementation.yaml"
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        del value["default_variant"]
        self.write_yaml("implementations/python/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "implementation", self.root_dir)

        self.assertIn("implementations/python/example/implementation.yaml", str(context.exception))
        self.assertIn("default_variant", str(context.exception))

    def test_read_contract_rejects_non_object_documents(self):
        path = self.root_dir / "contracts/build-profiles/build.yaml"
        path.write_text("- not\n- an object\n")

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "build-profile", self.root_dir)

        self.assertIn("contracts/build-profiles/build.yaml", str(context.exception))
        self.assertIn("$: expected an object", str(context.exception))

    def test_validate_repository_discovers_every_contract_kind(self):
        documents = validate_repository_contracts(self.root_dir)

        self.assertEqual(
            [(document.kind, document.path.relative_to(self.root_dir).as_posix()) for document in documents],
            [
                ("build-profile", "contracts/build-profiles/build.yaml"),
                ("environment-profile", "contracts/environment-profiles/local.yaml"),
                ("load-profile", "contracts/load-profiles/development.yaml"),
                ("measurement-protocol", "contracts/measurement-protocols/service.yaml"),
                ("implementation", "implementations/python/example/implementation.yaml"),
                ("variant", "implementations/python/example/variants/default.yaml"),
                ("scenario", "scenarios/example-scenario/scenario.yaml"),
            ],
        )

    def test_validate_repository_rejects_duplicate_contract_identity(self):
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        self.write_yaml("implementations/ruby/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("duplicate implementation identity", str(context.exception))
        self.assertIn("implementations/python/example/implementation.yaml", str(context.exception))
        self.assertIn("implementations/ruby/example/implementation.yaml", str(context.exception))

    def test_validate_repository_rejects_duplicate_contract_id_at_a_different_version(self):
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        value["contract_version"] = "2.0"
        self.write_yaml("implementations/ruby/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("duplicate implementation identity", str(context.exception))
        self.assertIn("python/example", str(context.exception))

    def test_validate_repository_requires_implementation_identity_to_match_path(self):
        implementation = self.read_yaml("implementations/python/example/implementation.yaml")
        implementation.update(
            {"id": "ruby/other", "language": "ruby", "framework": "other"}
        )
        self.write_yaml("implementations/python/example/implementation.yaml", implementation)
        variant = self.read_yaml("implementations/python/example/variants/default.yaml")
        variant["implementation"] = "ruby/other"
        self.write_yaml("implementations/python/example/variants/default.yaml", variant)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        errors = str(context.exception).splitlines()
        self.assertEqual(
            [error for error in errors if "implementations/python/example/implementation.yaml" in error],
            [
                "implementations/python/example/implementation.yaml: $.framework: "
                "implementation framework 'other' must match path 'example'",
                "implementations/python/example/implementation.yaml: $.id: "
                "implementation id 'ruby/other' must match path 'python/example'",
                "implementations/python/example/implementation.yaml: $.language: "
                "implementation language 'ruby' must match path 'python'",
            ],
        )

    def test_validate_repository_rejects_variant_filename_id_mismatch(self):
        value = self.read_yaml("implementations/python/example/variants/default.yaml")
        (self.root_dir / "implementations/python/example/variants/default.yaml").unlink()
        self.write_yaml("implementations/python/example/variants/not-default.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("implementations/python/example/variants/not-default.yaml", str(context.exception))
        self.assertIn("filename", str(context.exception))

    def test_validate_repository_rejects_profile_filename_id_mismatch(self):
        value = self.read_yaml("contracts/load-profiles/development.yaml")
        (self.root_dir / "contracts/load-profiles/development.yaml").unlink()
        self.write_yaml("contracts/load-profiles/not-development.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("contracts/load-profiles/not-development.yaml", str(context.exception))
        self.assertIn("filename", str(context.exception))

    def test_validate_repository_rejects_scenario_directory_id_mismatch(self):
        value = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        shutil.rmtree(self.root_dir / "scenarios/example-scenario")
        self.write_yaml("scenarios/not-example-scenario/scenario.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("scenarios/not-example-scenario/scenario.yaml", str(context.exception))
        self.assertIn("directory", str(context.exception))

    def test_validate_repository_rejects_missing_referenced_profile(self):
        value = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        value["contracts"]["load_profile"] = "missing"
        self.write_yaml("scenarios/example-scenario/scenario.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("scenarios/example-scenario/scenario.yaml", str(context.exception))
        self.assertIn("contracts.load_profile", str(context.exception))
        self.assertIn("missing load-profile", str(context.exception))

    def test_validate_repository_rejects_missing_default_variant(self):
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        value["default_variant"] = "missing"
        self.write_yaml("implementations/python/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("implementations/python/example/implementation.yaml", str(context.exception))
        self.assertIn("default_variant", str(context.exception))
        self.assertIn("missing variant", str(context.exception))

    def test_validate_repository_rejects_variant_for_different_implementation(self):
        value = self.read_yaml("implementations/python/example/variants/default.yaml")
        value["implementation"] = "python/other"
        self.write_yaml("implementations/python/example/variants/default.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("implementations/python/example/variants/default.yaml", str(context.exception))
        self.assertIn("implementation", str(context.exception))
        self.assertIn("missing variant 'default'", str(context.exception))

    def test_validate_repository_rejects_orphan_variant(self):
        (self.root_dir / "implementations/python/example/implementation.yaml").unlink()

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("implementations/python/example/variants/default.yaml", str(context.exception))
        self.assertIn("missing valid sibling implementation.yaml", str(context.exception))

    def test_validate_repository_aggregates_schema_and_independent_reference_errors(self):
        scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        scenario["contracts"]["load_profile"] = "missing"
        self.write_yaml("scenarios/example-scenario/scenario.yaml", scenario)
        invalid_scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        invalid_scenario["id"] = "invalid-scenario"
        del invalid_scenario["contracts"]
        self.write_yaml("scenarios/invalid-scenario/scenario.yaml", invalid_scenario)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        errors = str(context.exception).splitlines()
        self.assertEqual(errors, sorted(errors))
        self.assertEqual(len(errors), 2)
        self.assertIn("scenarios/example-scenario/scenario.yaml: $.contracts.load_profile", errors[0])
        self.assertIn("missing load-profile 'missing'", errors[0])
        self.assertIn("scenarios/invalid-scenario/scenario.yaml: $:", errors[1])
        self.assertIn("'contracts' is a required property", errors[1])

    def test_validate_repository_aggregates_errors_in_path_order(self):
        implementation = self.read_yaml("implementations/python/example/implementation.yaml")
        implementation["default_variant"] = "missing"
        self.write_yaml("implementations/python/example/implementation.yaml", implementation)
        variant = self.read_yaml("implementations/python/example/variants/default.yaml")
        variant["implementation"] = "python/other"
        self.write_yaml("implementations/python/example/variants/default.yaml", variant)
        scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        scenario["contracts"]["build_profile"] = "missing"
        self.write_yaml("scenarios/example-scenario/scenario.yaml", scenario)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        errors = str(context.exception).splitlines()
        self.assertEqual(errors, sorted(errors))
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main()
