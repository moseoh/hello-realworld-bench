from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator

from .build_config import BuildRunConfig, resolve_build_run_config
from .contracts import ContractDocument
from .manifest import ManifestValidationError, read_git_provenance


_CONTRACT_ROLES = (
    "implementation",
    "variant",
    "environment_profile",
    "measurement_protocol",
    "build_profile",
)


def build_resolved_build_manifest(
    config: BuildRunConfig,
    run_id: str,
    source: Mapping[str, object],
) -> dict[str, object]:
    contracts = {
        role: _contract_ref(config.selected_contracts[role], config.root_dir, role)
        for role in _CONTRACT_ROLES
    }
    cohort_payload = {"evidence_family": "build", "contracts": contracts}
    cohort = {
        **cohort_payload,
        "fingerprint": _canonical_digest(cohort_payload),
    }
    manifest_payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "source": dict(source),
        "selection": {
            "implementation": config.implementation,
            "variant": config.variant,
            "environment_profile": config.environment_profile_config["id"],
            "measurement_protocol": config.measurement_protocol_config["id"],
            "build_profile": config.build_profile_config["id"],
        },
        "contracts": contracts,
        "execution": {
            "app_dir": _repository_path_without_symlinks(
                config.app_dir,
                config.root_dir,
                "$.execution.app_dir",
            ),
            "variant_file": _repository_path_without_symlinks(
                config.variant_file,
                config.root_dir,
                "$.execution.variant_file",
            ),
            "build": config.build,
        },
        "cohort": cohort,
    }
    manifest_payload = _canonical_copy(manifest_payload)
    return _canonical_copy(
        {
            **manifest_payload,
            "manifest_digest": _canonical_digest(manifest_payload),
        }
    )


def validate_resolved_build_manifest(manifest: object, root: Path) -> None:
    schema = json.loads(
        (root / "contracts/schemas/build-resolved-run-manifest.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise ManifestValidationError(
            [
                f"{_json_location(error.absolute_path)}: {error.message}"
                for error in errors
            ]
        )

    assert isinstance(manifest, dict)
    validation_errors = _repository_path_errors(manifest)
    cohort = manifest["cohort"]
    contracts = manifest["contracts"]
    assert isinstance(cohort, dict)
    assert isinstance(contracts, dict)
    if cohort["contracts"] != contracts:
        validation_errors.append("$.cohort.contracts: must match $.contracts")

    cohort_payload = {key: value for key, value in cohort.items() if key != "fingerprint"}
    if cohort["fingerprint"] != _canonical_digest(cohort_payload):
        validation_errors.append("$.cohort.fingerprint: does not match the cohort payload")

    manifest_payload = {
        key: value for key, value in manifest.items() if key != "manifest_digest"
    }
    if manifest["manifest_digest"] != _canonical_digest(manifest_payload):
        validation_errors.append(
            "$.manifest_digest: does not match the manifest payload"
        )
    if validation_errors:
        raise ManifestValidationError(validation_errors)

    selection = manifest["selection"]
    assert isinstance(selection, dict)
    try:
        config = resolve_build_run_config(
            str(selection["implementation"]),
            str(selection["variant"]),
            root,
            environment_profile=str(selection["environment_profile"]),
            measurement_protocol=str(selection["measurement_protocol"]),
            build_profile=str(selection["build_profile"]),
        )
    except ValueError as error:
        raise ManifestValidationError([f"$.selection: {error}"]) from error

    expected = build_resolved_build_manifest(
        config,
        str(manifest["run_id"]),
        read_git_provenance(root),
    )
    mismatches = _resolved_manifest_mismatches(manifest, expected)
    if mismatches:
        raise ManifestValidationError(mismatches)


def _contract_ref(
    document: ContractDocument,
    root: Path,
    role: str,
) -> dict[str, str]:
    location = f"$.contracts.{role}.path"
    relative_path = _repository_path_without_symlinks(document.path, root, location)
    if not document.path.is_file():
        raise ManifestValidationError(
            [f"{location}: selected contract file is missing: {relative_path}"]
        )
    return {
        "kind": document.kind,
        "id": str(document.value["id"]),
        "schema_version": str(document.value["schema_version"]),
        "contract_version": str(document.value["contract_version"]),
        "digest": document.digest,
        "path": relative_path,
    }


def _repository_path_without_symlinks(path: Path, root: Path, location: str) -> str:
    repository_root = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(path))
    try:
        relative_path = candidate.relative_to(repository_root)
    except ValueError:
        raise ManifestValidationError(
            [f"{location}: path must remain inside the repository: {path}"]
        ) from None

    current = repository_root
    if current.is_symlink():
        raise ManifestValidationError(
            [
                f"{location}: symlink components are not allowed: "
                f"{relative_path.as_posix()}"
            ]
        )
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            raise ManifestValidationError(
                [
                    f"{location}: symlink components are not allowed: "
                    f"{relative_path.as_posix()}"
                ]
            )
    return relative_path.as_posix()


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_copy(value: object):
    return json.loads(_canonical_json(value))


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _repository_path_errors(manifest: dict[str, object]) -> list[str]:
    errors: list[str] = []

    def visit(value: object, location: str) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                child_location = f"{location}.{key}"
                child = value[key]
                if key in {"path", "app_dir", "variant_file"} and isinstance(child, str):
                    if not _is_canonical_repository_path(child):
                        errors.append(
                            f"{child_location}: must be a normalized "
                            "repository-relative POSIX path"
                        )
                visit(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{location}[{index}]")

    visit(manifest, "$")
    return errors


def _is_canonical_repository_path(path: str) -> bool:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or unicodedata.normalize("NFC", path) != path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        return False
    return all(part not in {"", ".", ".."} for part in path.split("/"))


def _resolved_manifest_mismatches(
    submitted: object,
    expected: object,
    location: str = "$",
) -> list[str]:
    if isinstance(submitted, dict) and isinstance(expected, dict):
        errors: list[str] = []
        for key in sorted(submitted.keys() | expected.keys()):
            child_location = f"{location}.{key}"
            if key not in submitted or key not in expected:
                errors.append(
                    f"{child_location}: does not match the resolved repository manifest"
                )
                continue
            errors.extend(
                _resolved_manifest_mismatches(
                    submitted[key],
                    expected[key],
                    child_location,
                )
            )
        return errors
    if isinstance(submitted, list) and isinstance(expected, list):
        errors = []
        if len(submitted) != len(expected):
            errors.append(
                f"{location}: expected {len(expected)} items, got {len(submitted)}"
            )
        for index, (submitted_item, expected_item) in enumerate(
            zip(submitted, expected)
        ):
            errors.extend(
                _resolved_manifest_mismatches(
                    submitted_item,
                    expected_item,
                    f"{location}[{index}]",
                )
            )
        return errors
    if submitted != expected:
        return [f"{location}: does not match the resolved repository manifest"]
    return []


def _json_location(path) -> str:
    location = "$"
    for part in path:
        location += f"[{part}]" if isinstance(part, int) else f".{part}"
    return location
