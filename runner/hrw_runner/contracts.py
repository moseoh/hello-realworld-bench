from __future__ import annotations

import hashlib
import json
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

_PROFILE_REFERENCE_KINDS = {
    "environment_profile": "environment-profile",
    "measurement_protocol": "measurement-protocol",
    "load_profile": "load-profile",
    "build_profile": "build-profile",
}


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
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def read_contract(path: Path, kind: str, root_dir: Path) -> ContractDocument:
    value = yaml.safe_load(path.read_text())
    display_path = _display_path(path, root_dir)
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

    return ContractDocument(kind, path, value, canonical_contract_digest(value))


def validate_repository_contracts(root_dir: Path) -> list[ContractDocument]:
    root = root_dir
    documents: list[ContractDocument] = []
    errors: list[str] = []

    for kind, path in _discover_contract_paths(root):
        try:
            documents.append(read_contract(path, kind, root))
        except ContractValidationError as error:
            errors.extend(error.errors)

    errors.extend(_validate_document_identities(documents, root))
    errors.extend(_validate_document_paths(documents, root))
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


def _validate_document_identities(
    documents: list[ContractDocument], root_dir: Path
) -> list[str]:
    errors: list[str] = []
    identities: dict[tuple[str, str], ContractDocument] = {}
    for document in documents:
        identity = (document.kind, str(document.value["id"]))
        original = identities.get(identity)
        if original is None:
            identities[identity] = document
            continue
        errors.append(
            f"{_display_path(document.path, root_dir)}: $.id: duplicate "
            f"{document.kind} identity ({identity[1]}); already defined by "
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
        if document.kind in {*_PROFILE_REFERENCE_KINDS.values(), "variant"}:
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


def _validate_references(
    documents: list[ContractDocument], root_dir: Path
) -> list[str]:
    errors: list[str] = []
    profiles = {
        kind: {str(document.value["id"]): document for document in documents if document.kind == kind}
        for kind in _PROFILE_REFERENCE_KINDS.values()
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
        if document.kind == "scenario":
            contracts = document.value["contracts"]
            if not isinstance(contracts, dict):
                continue
            for key, kind in _PROFILE_REFERENCE_KINDS.items():
                profile_id = str(contracts[key])
                if profile_id not in profiles[kind]:
                    errors.append(
                        f"{_display_path(document.path, root_dir)}: $.contracts.{key}: "
                        f"missing {kind} '{profile_id}'"
                    )
    return errors


def _display_path(path: Path, root_dir: Path) -> str:
    return path.resolve().relative_to(root_dir.resolve()).as_posix()


def _json_location(path) -> str:
    if not path:
        return "$"
    return "$." + ".".join(str(part) for part in path)


def _schema_error_sort_key(error) -> tuple[tuple[str, ...], str]:
    return tuple(str(part) for part in error.absolute_path), error.message
