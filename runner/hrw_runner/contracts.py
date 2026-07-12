from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


_SCHEMA_FILENAMES = {
    "implementation": "implementation.schema.json",
    "variant": "variant.schema.json",
    "scenario": "scenario.schema.json",
    "load-profile": "load-profile.schema.json",
    "environment-profile": "environment-profile.schema.json",
    "measurement-protocol": "measurement-protocol.schema.json",
    "build-profile": "build-profile.schema.json",
}

_DISCOVERY_PATTERNS = (
    ("implementation", "implementations/*/*/implementation.yaml"),
    ("variant", "implementations/*/*/variants/*.yaml"),
    ("scenario", "scenarios/*/scenario.yaml"),
    ("load-profile", "contracts/load-profiles/*.yaml"),
    ("environment-profile", "contracts/environment-profiles/*.yaml"),
    ("measurement-protocol", "contracts/measurement-protocols/*.yaml"),
    ("build-profile", "contracts/build-profiles/*.yaml"),
)

_SCENARIO_PROFILE_REFERENCE_KINDS = {
    "environment_profile": "environment-profile",
    "measurement_protocol": "measurement-protocol",
    "load_profile": "load-profile",
}

_PROFILE_KINDS = {*_SCENARIO_PROFILE_REFERENCE_KINDS.values(), "build-profile"}

_YAML_MERGE_TAG = "tag:yaml.org,2002:merge"
_YAML_MERGE_KEY = object()


class _UniqueKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == _YAML_MERGE_TAG:
                key = _YAML_MERGE_KEY
                display_key = "<<"
            else:
                key = self.construct_object(key_node, deep=deep)
                display_key = key
            try:
                duplicate = key in seen
            except TypeError:
                continue
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {display_key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


@dataclass(frozen=True)
class ContractDocument:
    kind: str
    path: Path
    value: dict[str, object]
    digest: str


class ContractValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(sorted(errors))
        super().__init__("\n".join(self.errors))


def canonical_contract_digest(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def read_contract(path: Path, kind: str, root_dir: Path) -> ContractDocument:
    display_path = _display_path(path, root_dir)
    try:
        value = yaml.load(path.read_text(), Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as error:
        problem = getattr(error, "problem", None) or str(error).splitlines()[0]
        mark = getattr(error, "problem_mark", None)
        location = (
            f" at line {mark.line + 1}, column {mark.column + 1}"
            if mark is not None
            else ""
        )
        raise ContractValidationError(
            [f"{display_path}: $: invalid YAML{location}: {problem}"]
        ) from error
    if not isinstance(value, dict):
        raise ContractValidationError([f"{display_path}: $: expected an object"])

    schema_path = root_dir / "contracts" / "schemas" / _SCHEMA_FILENAMES[kind]
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=_schema_error_sort_key)
    if errors:
        raise ContractValidationError(
            [
                f"{display_path}: {_json_location(error.absolute_path)}: {error.message}"
                for error in errors
            ]
        )

    finite_number_errors = _validate_finite_numbers(value)
    if finite_number_errors:
        raise ContractValidationError(
            [f"{display_path}: {error}" for error in finite_number_errors]
        )

    if kind == "load-profile":
        semantic_errors = _validate_load_profile_semantics(value)
        if semantic_errors:
            raise ContractValidationError(
                [f"{display_path}: {error}" for error in semantic_errors]
            )

    return ContractDocument(kind, path, value, canonical_contract_digest(value))


def validate_repository_contracts(root_dir: Path) -> list[ContractDocument]:
    root = root_dir
    documents: list[ContractDocument] = []
    errors = _validate_required_contract_paths(root)

    for kind, path in _discover_contract_paths(root):
        try:
            documents.append(read_contract(path, kind, root))
        except ContractValidationError as error:
            errors.extend(error.errors)

    errors.extend(_validate_document_identities(documents, root))
    errors.extend(_validate_document_paths(documents, root))
    errors.extend(_validate_scenario_load_scripts(documents, root))
    errors.extend(_validate_references(documents, root))
    if errors:
        raise ContractValidationError(errors)
    return documents


def _discover_contract_paths(root_dir: Path) -> list[tuple[str, Path]]:
    paths = [
        (kind, path)
        for kind, pattern in _DISCOVERY_PATTERNS
        for path in root_dir.glob(pattern)
    ]
    return sorted(paths, key=lambda item: _display_path(item[1], root_dir))


def _validate_required_contract_paths(root_dir: Path) -> list[str]:
    required_paths = [
        ("scenario", directory / "scenario.yaml")
        for directory in root_dir.glob("scenarios/*")
        if directory.is_dir()
    ]
    required_paths.extend(
        ("implementation", directory / "implementation.yaml")
        for directory in root_dir.glob("implementations/*/*")
        if directory.is_dir()
    )
    return [
        f"{_display_path(path, root_dir)}: $: missing required {kind} contract"
        for kind, path in required_paths
        if not path.is_file()
    ]


def _validate_document_identities(
    documents: list[ContractDocument], root_dir: Path
) -> list[str]:
    errors: list[str] = []
    identities: dict[tuple[str, ...], ContractDocument] = {}
    for document in documents:
        document_id = str(document.value["id"])
        if document.kind == "variant":
            identity = (
                document.kind,
                str(document.value["implementation"]),
                document_id,
            )
            display_identity = f"{identity[1]}, {identity[2]}"
        else:
            identity = (document.kind, document_id)
            display_identity = document_id
        original = identities.get(identity)
        if original is None:
            identities[identity] = document
            continue
        errors.append(
            f"{_display_path(document.path, root_dir)}: $.id: duplicate "
            f"{document.kind} identity ({display_identity}); already defined by "
            f"{_display_path(original.path, root_dir)}"
        )
    return errors


def _validate_document_paths(
    documents: list[ContractDocument], root_dir: Path
) -> list[str]:
    errors: list[str] = []
    for document in documents:
        document_id = str(document.value["id"])
        display_path = _display_path(document.path, root_dir)
        if document.kind in {*_PROFILE_KINDS, "variant"}:
            if document.path.stem != document_id:
                errors.append(
                    f"{display_path}: $.id: filename '{document.path.stem}.yaml' must "
                    f"match id '{document_id}.yaml'"
                )
        if document.kind == "scenario" and document.path.parent.name != document_id:
            errors.append(
                f"{display_path}: $.id: directory '{document.path.parent.name}' must "
                f"match id '{document_id}'"
            )
        if document.kind == "implementation":
            language = document.path.parent.parent.name
            framework = document.path.parent.name
            expected_id = f"{language}/{framework}"
            if document_id != expected_id:
                errors.append(
                    f"{display_path}: $.id: implementation id '{document_id}' must "
                    f"match path '{expected_id}'"
                )
            if document.value["language"] != language:
                errors.append(
                    f"{display_path}: $.language: implementation language "
                    f"'{document.value['language']}' must match path '{language}'"
                )
            if document.value["framework"] != framework:
                errors.append(
                    f"{display_path}: $.framework: implementation framework "
                    f"'{document.value['framework']}' must match path '{framework}'"
                )
    return errors


def _validate_scenario_load_scripts(
    documents: list[ContractDocument], root_dir: Path
) -> list[str]:
    errors: list[str] = []
    for document in documents:
        if document.kind != "scenario":
            continue
        load = document.value["load"]
        if not isinstance(load, dict) or load.get("enabled") is not True:
            continue

        script = str(load["script"])
        display_path = _display_path(document.path, root_dir)
        error_prefix = f"{display_path}: $.load.script:"
        parts = script.split("/")
        scenario_id = str(document.value["id"])
        expected_prefix = ["scenarios", scenario_id]

        if (
            script.startswith("/")
            or "\\" in script
            or any(part in {"", ".", ".."} for part in parts)
        ):
            errors.append(
                f"{error_prefix} must be a canonical POSIX repository-relative path"
            )
            continue
        if parts[:2] != expected_prefix or len(parts) < 3:
            errors.append(
                f"{error_prefix} must be under scenarios/{scenario_id}/"
            )
            continue
        if not script.endswith(".js"):
            errors.append(f"{error_prefix} must end in .js")
            continue

        script_path = root_dir / script
        scenario_dir = document.path.parent.resolve()
        resolved_script = script_path.resolve()
        try:
            resolved_script.relative_to(scenario_dir)
        except ValueError:
            errors.append(
                f"{error_prefix} resolved path must remain under scenarios/{scenario_id}/"
            )
            continue
        if not resolved_script.is_file():
            errors.append(f"{error_prefix} must reference an existing regular file")

    return errors


def _validate_references(
    documents: list[ContractDocument], root_dir: Path
) -> list[str]:
    errors: list[str] = []
    profiles = {
        kind: {str(document.value["id"]): document for document in documents if document.kind == kind}
        for kind in _PROFILE_KINDS
    }
    implementations_by_path = {
        document.path.parent: document
        for document in documents
        if document.kind == "implementation"
    }
    variants_by_implementation_path: dict[Path, set[str]] = {}

    for document in documents:
        if document.kind != "variant":
            continue
        implementation = implementations_by_path.get(document.path.parents[1])
        if implementation is None:
            errors.append(
                f"{_display_path(document.path, root_dir)}: $.implementation: "
                "missing valid sibling implementation.yaml"
            )
            continue
        expected_implementation = str(implementation.value["id"])
        if document.value["implementation"] != expected_implementation:
            errors.append(
                f"{_display_path(document.path, root_dir)}: $.implementation: variant "
                f"implementation '{document.value['implementation']}' does not match "
                f"'{expected_implementation}'"
            )
            continue
        variants_by_implementation_path.setdefault(implementation.path.parent, set()).add(
            str(document.value["id"])
        )

    for document in documents:
        if document.kind == "implementation":
            default_variant = str(document.value["default_variant"])
            variant_ids = variants_by_implementation_path.get(document.path.parent, set())
            if default_variant not in variant_ids:
                errors.append(
                    f"{_display_path(document.path, root_dir)}: $.default_variant: "
                    f"missing variant '{default_variant}'"
                )
            default_build_profile = str(document.value["default_build_profile"])
            if default_build_profile not in profiles["build-profile"]:
                errors.append(
                    f"{_display_path(document.path, root_dir)}: "
                    f"$.default_build_profile: missing build-profile "
                    f"'{default_build_profile}'"
                )
        if document.kind == "scenario":
            default_profiles = document.value["default_profiles"]
            if not isinstance(default_profiles, dict):
                continue
            for key, kind in _SCENARIO_PROFILE_REFERENCE_KINDS.items():
                profile_id = str(default_profiles[key])
                if profile_id not in profiles[kind]:
                    errors.append(
                        f"{_display_path(document.path, root_dir)}: "
                        f"$.default_profiles.{key}: "
                        f"missing {kind} '{profile_id}'"
                    )
    return errors


def _validate_load_profile_semantics(value: dict[str, object]) -> list[str]:
    model = value["model"]
    executor = value["executor"]
    timing = value["timing"]
    phases = value["phases"]
    assert isinstance(timing, dict)
    assert isinstance(phases, list)
    errors: list[str] = []

    def add_error(path: tuple[str | int, ...], message: str) -> None:
        errors.append(f"{_json_location(path)}: {message}")

    if model == "disabled":
        if executor != "none":
            add_error(
                ("executor",),
                "must be 'none' when $.model is 'disabled'",
            )
        if timing.get("source") != "disabled":
            add_error(
                ("timing", "source"),
                "must be 'disabled' when $.model is 'disabled'",
            )
        for field in ("warmup_seconds", "measured_seconds"):
            if field in timing:
                add_error(
                    ("timing", field),
                    "must not be defined when $.model is 'disabled'",
                )
        if phases:
            add_error(
                ("phases",),
                "must be empty when $.model is 'disabled'",
            )
        return errors

    if model == "closed":
        if executor != "constant-vus":
            add_error(
                ("executor",),
                "must be 'constant-vus' when $.model is 'closed'",
            )
        if timing.get("source") != "scenario":
            add_error(
                ("timing", "source"),
                "must be 'scenario' when $.model is 'closed'",
            )
        for field in ("warmup_seconds", "measured_seconds"):
            if timing.get(field) is not None:
                add_error(
                    ("timing", field),
                    "must be null or omitted when $.model is 'closed'",
                )
        if not phases:
            add_error(
                ("phases",),
                "must contain at least one phase when $.model is 'closed'",
            )
        for index, phase in enumerate(phases):
            assert isinstance(phase, dict)
            if phase.get("source") != "scenario":
                add_error(
                    ("phases", index, "source"),
                    "must be 'scenario' when $.model is 'closed'",
                )
            for field in ("duration_seconds", "vus"):
                if phase.get(field) is not None:
                    add_error(
                        ("phases", index, field),
                        "must be null or omitted when $.model is 'closed'",
                    )
            if "multiplier" in phase:
                add_error(
                    ("phases", index, "multiplier"),
                    "must not be defined when $.model is 'closed'",
                )
        return errors

    if executor not in {"constant-arrival-rate", "ramping-arrival-rate"}:
        add_error(
            ("executor",),
            "must be 'constant-arrival-rate' or 'ramping-arrival-rate' when "
            "$.model is 'open'",
        )
    if "source" in timing:
        add_error(
            ("timing", "source"),
            "must not be defined when $.model is 'open'",
        )
    for field in ("warmup_seconds", "measured_seconds"):
        if not isinstance(timing.get(field), int):
            add_error(
                ("timing", field),
                "must be a fixed non-negative integer when $.model is 'open'",
            )
    if not phases:
        add_error(
            ("phases",),
            "must contain at least one phase when $.model is 'open'",
        )
    for index, phase in enumerate(phases):
        assert isinstance(phase, dict)
        if "source" in phase:
            add_error(
                ("phases", index, "source"),
                "must not be defined when $.model is 'open'",
            )
        duration = phase.get("duration_seconds")
        if not isinstance(duration, int) or duration <= 0:
            add_error(
                ("phases", index, "duration_seconds"),
                "must be greater than 0 when $.model is 'open'",
            )
        multiplier = phase.get("multiplier")
        if not isinstance(multiplier, (int, float)) or multiplier <= 0:
            add_error(
                ("phases", index, "multiplier"),
                "must be greater than 0 when $.model is 'open'",
            )
        if "vus" in phase:
            add_error(
                ("phases", index, "vus"),
                "must not be defined when $.model is 'open'",
            )

    measured_seconds = timing.get("measured_seconds")
    durations = [phase.get("duration_seconds") for phase in phases]
    if (
        phases
        and isinstance(measured_seconds, int)
        and all(isinstance(duration, int) for duration in durations)
    ):
        duration_sum = sum(durations)
        if duration_sum != measured_seconds:
            add_error(
                ("phases",),
                "duration_seconds values must sum to $.timing.measured_seconds "
                f"({measured_seconds}), got {duration_sum}",
            )
    return errors


def _validate_finite_numbers(
    value: object,
    path: tuple[str | int, ...] = (),
) -> list[str]:
    if isinstance(value, float):
        if not math.isfinite(value):
            return [f"{_json_location(path)}: must be a finite number"]
        return []
    if isinstance(value, dict):
        errors: list[str] = []
        for key, nested_value in value.items():
            errors.extend(
                _validate_finite_numbers(nested_value, (*path, str(key)))
            )
        return errors
    if isinstance(value, list):
        errors = []
        for index, nested_value in enumerate(value):
            errors.extend(_validate_finite_numbers(nested_value, (*path, index)))
        return errors
    return []


def _display_path(path: Path, root_dir: Path) -> str:
    return path.resolve().relative_to(root_dir.resolve()).as_posix()


def _json_location(path) -> str:
    location = "$"
    for part in path:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return location


def _schema_error_sort_key(error) -> tuple[tuple[str, ...], str]:
    return tuple(str(part) for part in error.absolute_path), error.message
