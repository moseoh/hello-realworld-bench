import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema.exceptions import SchemaError

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
                "default_build_profile": "build",
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
                "default_profiles": {
                    "environment_profile": "local",
                    "measurement_protocol": "service",
                    "load_profile": "development",
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

    def test_canonical_digest_rejects_non_finite_numbers(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonical_contract_digest({"value": value})

    def test_read_contract_reports_schema_error_with_path_and_location(self):
        path = self.root_dir / "implementations/python/example/implementation.yaml"
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        del value["default_variant"]
        self.write_yaml("implementations/python/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "implementation", self.root_dir)

        self.assertIn("implementations/python/example/implementation.yaml", str(context.exception))
        self.assertIn("default_variant", str(context.exception))

    def test_read_implementation_requires_default_build_profile(self):
        path = self.root_dir / "implementations/python/example/implementation.yaml"
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        del value["default_build_profile"]
        self.write_yaml("implementations/python/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "implementation", self.root_dir)

        self.assertIn("default_build_profile", str(context.exception))

    def test_read_scenario_rejects_a_build_profile_default(self):
        path = self.root_dir / "scenarios/example-scenario/scenario.yaml"
        value = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        value["default_profiles"]["build_profile"] = "build"
        self.write_yaml("scenarios/example-scenario/scenario.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "scenario", self.root_dir)

        self.assertIn("build_profile", str(context.exception))
        self.assertIn("was unexpected", str(context.exception))

    def test_read_contract_rejects_non_object_documents(self):
        path = self.root_dir / "contracts/build-profiles/build.yaml"
        path.write_text("- not\n- an object\n")

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "build-profile", self.root_dir)

        self.assertIn("contracts/build-profiles/build.yaml", str(context.exception))
        self.assertIn("$: expected an object", str(context.exception))

    def test_read_contract_reports_yaml_error_with_path_and_location(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        path.write_text("id: [\n")

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertIn("contracts/load-profiles/development.yaml", str(context.exception))
        self.assertIn("invalid YAML", str(context.exception))
        self.assertIn("line 2, column 1", str(context.exception))

    def test_read_contract_rejects_duplicate_nested_yaml_keys(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        path.write_text(
            "schema_version: '1.0'\n"
            "id: development\n"
            "contract_version: '1.0'\n"
            "description: Development load profile.\n"
            "status: development\n"
            "model: closed\n"
            "executor: constant-vus\n"
            "timing:\n"
            "  source: scenario\n"
            "  source: disabled\n"
            "phases: []\n"
        )

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(
            str(context.exception),
            "contracts/load-profiles/development.yaml: $: invalid YAML at line 10, "
            "column 3: found duplicate key 'source'",
        )

    def test_read_contract_preserves_yaml_merge_semantics(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        path.write_text(
            "schema_version: '1.0'\n"
            "id: development\n"
            "contract_version: '1.0'\n"
            "description: Development load profile.\n"
            "status: development\n"
            "model: closed\n"
            "executor: constant-vus\n"
            "timing:\n"
            "  source: scenario\n"
            "phases:\n"
            "  - &scenario_phase\n"
            "    source: scenario\n"
            "    duration_seconds: null\n"
            "    vus: null\n"
            "  - <<: *scenario_phase\n"
        )

        document = read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(document.value["phases"][0], document.value["phases"][1])

    def test_read_contract_rejects_duplicate_yaml_merge_keys(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        path.write_text(
            "schema_version: '1.0'\n"
            "id: development\n"
            "contract_version: '1.0'\n"
            "description: Development load profile.\n"
            "status: development\n"
            "model: closed\n"
            "executor: constant-vus\n"
            "timing:\n"
            "  source: scenario\n"
            "phases:\n"
            "  - &scenario_phase\n"
            "    source: scenario\n"
            "    duration_seconds: null\n"
            "    vus: null\n"
            "  - <<: *scenario_phase\n"
            "    <<: *scenario_phase\n"
        )

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(
            str(context.exception),
            "contracts/load-profiles/development.yaml: $: invalid YAML at line 16, "
            "column 5: found duplicate key '<<'",
        )

    def test_read_contract_does_not_wrap_invalid_schema_definition(self):
        schema_path = self.root_dir / "contracts/schemas/load-profile.schema.json"
        schema = json.loads(schema_path.read_text())
        schema["type"] = "not-a-json-schema-type"
        schema_path.write_text(json.dumps(schema))
        path = self.root_dir / "contracts/load-profiles/development.yaml"

        with self.assertRaises(SchemaError):
            read_contract(path, "load-profile", self.root_dir)

    def test_read_contract_accepts_all_catalog_load_profiles(self):
        profile_ids = [
            "burst-recovery",
            "capacity-ramp",
            "development-local",
            "none",
            "steady",
        ]

        documents = [
            read_contract(
                PROJECT_ROOT / "contracts/load-profiles" / f"{profile_id}.yaml",
                "load-profile",
                PROJECT_ROOT,
            )
            for profile_id in profile_ids
        ]

        self.assertEqual(
            [document.value["id"] for document in documents],
            profile_ids,
        )

    def test_read_contract_rejects_mixed_disabled_load_semantics(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        value = self.read_yaml("contracts/load-profiles/development.yaml")
        value.update(
            {
                "model": "disabled",
                "executor": "constant-vus",
                "timing": {
                    "source": "scenario",
                    "warmup_seconds": 0,
                    "measured_seconds": 0,
                },
            }
        )
        self.write_yaml("contracts/load-profiles/development.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(
            str(context.exception).splitlines(),
            [
                "contracts/load-profiles/development.yaml: $.executor: must be "
                "'none' when $.model is 'disabled'",
                "contracts/load-profiles/development.yaml: $.phases: must be empty "
                "when $.model is 'disabled'",
                "contracts/load-profiles/development.yaml: "
                "$.timing.measured_seconds: must not be defined when $.model is "
                "'disabled'",
                "contracts/load-profiles/development.yaml: $.timing.source: must be "
                "'disabled' when $.model is 'disabled'",
                "contracts/load-profiles/development.yaml: $.timing.warmup_seconds: "
                "must not be defined when $.model is 'disabled'",
            ],
        )

    def test_read_contract_rejects_mixed_closed_load_semantics(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        value = self.read_yaml("contracts/load-profiles/development.yaml")
        value.update(
            {
                "executor": "none",
                "timing": {
                    "source": "disabled",
                    "warmup_seconds": 10,
                    "measured_seconds": 20,
                },
                "phases": [
                    {
                        "source": "disabled",
                        "duration_seconds": 20,
                        "multiplier": 1.0,
                        "vus": 10,
                    }
                ],
            }
        )
        self.write_yaml("contracts/load-profiles/development.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(
            str(context.exception).splitlines(),
            [
                "contracts/load-profiles/development.yaml: $.executor: must be "
                "'constant-vus' when $.model is 'closed'",
                "contracts/load-profiles/development.yaml: "
                "$.phases[0].duration_seconds: must be null or omitted when "
                "$.model is 'closed'",
                "contracts/load-profiles/development.yaml: "
                "$.phases[0].multiplier: must not be defined when $.model is "
                "'closed'",
                "contracts/load-profiles/development.yaml: $.phases[0].source: "
                "must be 'scenario' when $.model is 'closed'",
                "contracts/load-profiles/development.yaml: $.phases[0].vus: must be "
                "null or omitted when $.model is 'closed'",
                "contracts/load-profiles/development.yaml: "
                "$.timing.measured_seconds: must be null or omitted when $.model "
                "is 'closed'",
                "contracts/load-profiles/development.yaml: $.timing.source: must be "
                "'scenario' when $.model is 'closed'",
                "contracts/load-profiles/development.yaml: $.timing.warmup_seconds: "
                "must be null or omitted when $.model is 'closed'",
            ],
        )

    def test_read_contract_requires_a_closed_load_phase(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        value = self.read_yaml("contracts/load-profiles/development.yaml")
        value["phases"] = []
        self.write_yaml("contracts/load-profiles/development.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(
            str(context.exception),
            "contracts/load-profiles/development.yaml: $.phases: must contain at "
            "least one phase when $.model is 'closed'",
        )

    def test_read_contract_rejects_mixed_open_load_semantics(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        value = self.read_yaml("contracts/load-profiles/development.yaml")
        value.update(
            {
                "model": "open",
                "executor": "none",
                "timing": {
                    "source": "scenario",
                    "warmup_seconds": None,
                    "measured_seconds": 20,
                },
                "phases": [
                    {
                        "source": "scenario",
                        "duration_seconds": 0,
                        "multiplier": 0,
                        "vus": 10,
                    }
                ],
            }
        )
        self.write_yaml("contracts/load-profiles/development.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(
            str(context.exception).splitlines(),
            [
                "contracts/load-profiles/development.yaml: $.executor: must be "
                "'constant-arrival-rate' or 'ramping-arrival-rate' when $.model is "
                "'open'",
                "contracts/load-profiles/development.yaml: $.phases: "
                "duration_seconds values must sum to $.timing.measured_seconds "
                "(20), got 0",
                "contracts/load-profiles/development.yaml: "
                "$.phases[0].duration_seconds: must be greater than 0 when $.model "
                "is 'open'",
                "contracts/load-profiles/development.yaml: "
                "$.phases[0].multiplier: must be greater than 0 when $.model is "
                "'open'",
                "contracts/load-profiles/development.yaml: $.phases[0].source: "
                "must not be defined when $.model is 'open'",
                "contracts/load-profiles/development.yaml: $.phases[0].vus: must "
                "not be defined when $.model is 'open'",
                "contracts/load-profiles/development.yaml: $.timing.source: must "
                "not be defined when $.model is 'open'",
                "contracts/load-profiles/development.yaml: $.timing.warmup_seconds: "
                "must be a fixed non-negative integer when $.model is 'open'",
            ],
        )

    def test_read_contract_requires_open_phase_durations_to_match_timing(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        value = self.read_yaml("contracts/load-profiles/development.yaml")
        value.update(
            {
                "model": "open",
                "executor": "constant-arrival-rate",
                "timing": {"warmup_seconds": 10, "measured_seconds": 20},
                "phases": [{"duration_seconds": 10, "multiplier": 1.0}],
            }
        )
        self.write_yaml("contracts/load-profiles/development.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "load-profile", self.root_dir)

        self.assertEqual(
            str(context.exception),
            "contracts/load-profiles/development.yaml: $.phases: duration_seconds "
            "values must sum to $.timing.measured_seconds (20), got 10",
        )

    def test_read_contract_rejects_non_finite_open_phase_multipliers(self):
        path = self.root_dir / "contracts/load-profiles/development.yaml"
        original = self.read_yaml("contracts/load-profiles/development.yaml")

        for multiplier in (float("nan"), float("inf")):
            with self.subTest(multiplier=multiplier):
                value = copy.deepcopy(original)
                value.update(
                    {
                        "model": "open",
                        "executor": "constant-arrival-rate",
                        "timing": {
                            "warmup_seconds": 10,
                            "measured_seconds": 20,
                        },
                        "phases": [
                            {"duration_seconds": 20, "multiplier": multiplier}
                        ],
                    }
                )
                self.write_yaml("contracts/load-profiles/development.yaml", value)

                with self.assertRaises(ContractValidationError) as context:
                    read_contract(path, "load-profile", self.root_dir)

                self.assertEqual(
                    str(context.exception),
                    "contracts/load-profiles/development.yaml: "
                    "$.phases[0].multiplier: must be a finite number",
                )

    def test_read_contract_rejects_non_finite_nested_scenario_numbers(self):
        path = self.root_dir / "scenarios/example-scenario/scenario.yaml"
        value = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        value["load"]["docker_stats_interval_seconds"] = float("inf")
        self.write_yaml("scenarios/example-scenario/scenario.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            read_contract(path, "scenario", self.root_dir)

        self.assertEqual(
            str(context.exception),
            "scenarios/example-scenario/scenario.yaml: "
            "$.load.docker_stats_interval_seconds: must be a finite number",
        )

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

    def test_validate_repository_requires_scenario_contract_in_each_directory(self):
        scenario_path = self.root_dir / "scenarios/example-scenario/scenario.yaml"
        scenario_path.rename(scenario_path.with_name("renamed.yaml"))

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertEqual(
            str(context.exception),
            "scenarios/example-scenario/scenario.yaml: $: missing required scenario "
            "contract",
        )

    def test_validate_repository_requires_implementation_contract_in_each_directory(
        self,
    ):
        implementation_path = (
            self.root_dir / "implementations/python/example/implementation.yaml"
        )
        implementation_path.rename(
            self.root_dir / "implementations/python/implementation.yaml"
        )

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn(
            "implementations/python/example/implementation.yaml: $: missing required "
            "implementation contract",
            context.exception.errors,
        )

    def test_validate_repository_ignores_unrelated_yaml(self):
        notes_path = self.root_dir / "scenarios/example-scenario/notes.yaml"
        notes_path.write_text("id: [\n")

        documents = validate_repository_contracts(self.root_dir)

        self.assertEqual(len(documents), 7)

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

    def test_validate_repository_rejects_missing_scenario_default_profiles(self):
        original = self.read_yaml("scenarios/example-scenario/scenario.yaml")

        for key, kind in (
            ("environment_profile", "environment-profile"),
            ("measurement_protocol", "measurement-protocol"),
            ("load_profile", "load-profile"),
        ):
            with self.subTest(key=key):
                value = copy.deepcopy(original)
                value["default_profiles"][key] = "missing"
                self.write_yaml("scenarios/example-scenario/scenario.yaml", value)

                with self.assertRaises(ContractValidationError) as context:
                    validate_repository_contracts(self.root_dir)

                self.assertIn("scenarios/example-scenario/scenario.yaml", str(context.exception))
                self.assertIn(f"default_profiles.{key}", str(context.exception))
                self.assertIn(f"missing {kind}", str(context.exception))

        self.write_yaml("scenarios/example-scenario/scenario.yaml", original)

    def test_validate_repository_rejects_missing_default_variant(self):
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        value["default_variant"] = "missing"
        self.write_yaml("implementations/python/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("implementations/python/example/implementation.yaml", str(context.exception))
        self.assertIn("default_variant", str(context.exception))
        self.assertIn("missing variant", str(context.exception))

    def test_validate_repository_rejects_missing_default_build_profile(self):
        value = self.read_yaml("implementations/python/example/implementation.yaml")
        value["default_build_profile"] = "missing"
        self.write_yaml("implementations/python/example/implementation.yaml", value)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertIn("implementations/python/example/implementation.yaml", str(context.exception))
        self.assertIn("default_build_profile", str(context.exception))
        self.assertIn("missing build-profile", str(context.exception))

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
        scenario["default_profiles"]["load_profile"] = "missing"
        self.write_yaml("scenarios/example-scenario/scenario.yaml", scenario)
        invalid_scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        invalid_scenario["id"] = "invalid-scenario"
        del invalid_scenario["default_profiles"]
        self.write_yaml("scenarios/invalid-scenario/scenario.yaml", invalid_scenario)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        errors = str(context.exception).splitlines()
        self.assertEqual(errors, sorted(errors))
        self.assertEqual(len(errors), 2)
        self.assertIn("scenarios/example-scenario/scenario.yaml: $.default_profiles.load_profile", errors[0])
        self.assertIn("missing load-profile 'missing'", errors[0])
        self.assertIn("scenarios/invalid-scenario/scenario.yaml: $:", errors[1])
        self.assertIn("'default_profiles' is a required property", errors[1])

    def test_validate_repository_aggregates_parse_schema_and_reference_errors(self):
        implementation = self.read_yaml("implementations/python/example/implementation.yaml")
        implementation["default_build_profile"] = "missing"
        self.write_yaml("implementations/python/example/implementation.yaml", implementation)
        scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        invalid_scenario = dict(scenario)
        invalid_scenario["id"] = "invalid-scenario"
        del invalid_scenario["default_profiles"]
        self.write_yaml("scenarios/invalid-scenario/scenario.yaml", invalid_scenario)
        malformed_path = self.root_dir / "contracts/load-profiles/malformed.yaml"
        malformed_path.write_text("id: [\n")

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        errors = str(context.exception).splitlines()
        self.assertEqual(errors, sorted(errors))
        self.assertEqual(len(errors), 3)
        self.assertIn("contracts/load-profiles/malformed.yaml", errors[0])
        self.assertIn("invalid YAML", errors[0])
        self.assertIn("implementations/python/example/implementation.yaml", errors[1])
        self.assertIn("missing build-profile 'missing'", errors[1])
        self.assertIn("scenarios/invalid-scenario/scenario.yaml", errors[2])
        self.assertIn("'default_profiles' is a required property", errors[2])

    def test_validate_repository_aggregates_duplicate_key_and_schema_errors(self):
        duplicate_path = self.root_dir / "contracts/load-profiles/duplicate.yaml"
        duplicate_path.write_text("id: first\nid: second\n")
        scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        del scenario["default_profiles"]
        self.write_yaml("scenarios/example-scenario/scenario.yaml", scenario)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertEqual(
            str(context.exception).splitlines(),
            [
                "contracts/load-profiles/duplicate.yaml: $: invalid YAML at line 2, "
                "column 1: found duplicate key 'id'",
                "scenarios/example-scenario/scenario.yaml: $: 'default_profiles' is "
                "a required property",
            ],
        )

    def test_validate_repository_aggregates_missing_parse_schema_and_reference_errors(
        self,
    ):
        implementation = self.read_yaml(
            "implementations/python/example/implementation.yaml"
        )
        implementation["default_build_profile"] = "missing"
        self.write_yaml(
            "implementations/python/example/implementation.yaml",
            implementation,
        )
        scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        scenario_path = self.root_dir / "scenarios/example-scenario/scenario.yaml"
        scenario_path.rename(scenario_path.with_name("renamed.yaml"))
        invalid_scenario = copy.deepcopy(scenario)
        invalid_scenario["id"] = "invalid-scenario"
        del invalid_scenario["default_profiles"]
        self.write_yaml("scenarios/invalid-scenario/scenario.yaml", invalid_scenario)
        malformed_path = self.root_dir / "contracts/load-profiles/malformed.yaml"
        malformed_path.write_text("id: [\n")

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        self.assertEqual(
            str(context.exception).splitlines(),
            [
                "contracts/load-profiles/malformed.yaml: $: invalid YAML at line 2, "
                "column 1: expected the node content, but found '<stream end>'",
                "implementations/python/example/implementation.yaml: "
                "$.default_build_profile: missing build-profile 'missing'",
                "scenarios/example-scenario/scenario.yaml: $: missing required "
                "scenario contract",
                "scenarios/invalid-scenario/scenario.yaml: $: 'default_profiles' is "
                "a required property",
            ],
        )

    def test_validate_repository_aggregates_errors_in_path_order(self):
        implementation = self.read_yaml("implementations/python/example/implementation.yaml")
        implementation["default_variant"] = "missing"
        self.write_yaml("implementations/python/example/implementation.yaml", implementation)
        variant = self.read_yaml("implementations/python/example/variants/default.yaml")
        variant["implementation"] = "python/other"
        self.write_yaml("implementations/python/example/variants/default.yaml", variant)
        scenario = self.read_yaml("scenarios/example-scenario/scenario.yaml")
        scenario["default_profiles"]["load_profile"] = "missing"
        self.write_yaml("scenarios/example-scenario/scenario.yaml", scenario)

        with self.assertRaises(ContractValidationError) as context:
            validate_repository_contracts(self.root_dir)

        errors = str(context.exception).splitlines()
        self.assertEqual(errors, sorted(errors))
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main()
