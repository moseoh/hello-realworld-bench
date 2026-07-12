from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator

from .commands import run
from .config import RunConfig, resolve_run_config
from .contracts import ContractDocument


_CONTRACT_ROLES = (
    "implementation",
    "variant",
    "scenario",
    "environment_profile",
    "measurement_protocol",
    "load_profile",
    "build_profile",
)
_COHORT_CONTRACT_ROLES = (
    "scenario",
    "load_profile",
    "environment_profile",
    "measurement_protocol",
)
_COHORT_ASSET_ROLES = {
    "environment-compose",
    "scenario-compose",
    "scenario-file",
}
_ASSET_ROLE_ORDER = {
    "environment-compose": 0,
    "implementation-compose": 1,
    "variant-compose": 2,
    "scenario-compose": 3,
    "scenario-file": 4,
}


class ManifestValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(sorted(errors))
        super().__init__("\n".join(self.errors))


def read_git_provenance(root: Path) -> dict[str, object]:
    git_commit = run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture=True,
    ).stdout.strip()
    tracked_status = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=root,
        capture=True,
    ).stdout
    tracked = run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=root,
        capture=True,
    ).stdout
    untracked = run(
        ["git", "ls-files", "-z", "--others", "--exclude-per-directory=.gitignore"],
        cwd=root,
        capture=True,
    ).stdout
    untracked_paths = [path for path in untracked.split("\0") if path]

    snapshot = hashlib.sha256()
    paths = {path for path in tracked.split("\0") if path}
    paths.update(untracked_paths)
    for relative_path in sorted(paths):
        path = root / relative_path
        try:
            stat = path.lstat()
        except FileNotFoundError:
            _update_snapshot(snapshot, relative_path, "deleted", "", b"")
            continue

        if path.is_symlink():
            content = os.fsencode(os.readlink(path))
            _update_snapshot(snapshot, relative_path, "symlink", "", content)
        elif path.is_file():
            executable = "executable" if stat.st_mode & 0o111 else "regular"
            _update_snapshot(snapshot, relative_path, "file", executable, path.read_bytes())
        else:
            _update_snapshot(snapshot, relative_path, "other", "", b"")

    return {
        "git_commit": git_commit,
        "git_dirty": bool(tracked_status or untracked_paths),
        "worktree_digest": snapshot.hexdigest(),
    }


def resolve_input_assets(config: RunConfig) -> list[dict[str, str]]:
    root = Path(os.path.abspath(config.root_dir))
    compose_files = (
        (
            "environment-compose",
            root / "infra/docker-compose.base.yml",
            True,
        ),
        (
            "implementation-compose",
            root / "infra" / f"docker-compose.{config.compose_profile}.yml",
            True,
        ),
        (
            "variant-compose",
            root
            / "infra"
            / f"docker-compose.{config.compose_profile}.{config.variant}.yml",
            False,
        ),
        (
            "scenario-compose",
            root / "infra" / f"docker-compose.{config.scenario}.yml",
            False,
        ),
    )

    assets: list[dict[str, str]] = []
    for role, path, required in compose_files:
        relative_path = _repository_path_without_symlinks(path, root, "$.assets")
        if not path.is_file():
            if required:
                raise ManifestValidationError(
                    [f"$.assets: required asset is missing: {relative_path}"]
                )
            continue
        assets.append(_asset_ref(role, path, relative_path))

    _repository_path_without_symlinks(config.scenario_dir, root, "$.assets")
    scenario_dir = Path(os.path.abspath(config.scenario_dir))

    for path in sorted(scenario_dir.rglob("*")):
        relative_path = _repository_path_without_symlinks(path, root, "$.assets")
        relative_to_scenario = path.relative_to(scenario_dir)
        if relative_to_scenario.parts in {("README.md",), ("scenario.yaml",)}:
            continue
        if path.is_file():
            assets.append(_asset_ref("scenario-file", path, relative_path))

    return sorted(
        assets,
        key=lambda asset: (_ASSET_ROLE_ORDER[asset["role"]], asset["path"]),
    )


def build_resolved_manifest(
    config: RunConfig,
    run_id: str,
    source: Mapping[str, object],
) -> dict[str, object]:
    contracts = {
        role: _contract_ref(config.selected_contracts[role], config.root_dir, role)
        for role in _CONTRACT_ROLES
    }
    assets = resolve_input_assets(config)
    runtime = dict(config.runtime)
    runtime.update({"language": config.language, "framework": config.framework})
    services = config.scenario_config.get("services")
    if not isinstance(services, dict):
        raise ManifestValidationError(["$.execution.services: expected an object"])

    cohort_payload = {
        "evidence_family": config.measurement_protocol_config["evidence_family"],
        "contracts": {
            role: contracts[role]
            for role in _COHORT_CONTRACT_ROLES
        },
        "assets": [
            asset for asset in assets if asset["role"] in _COHORT_ASSET_ROLES
        ],
    }
    cohort_payload = _canonical_copy(cohort_payload)
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
            "scenario": config.scenario,
            "environment_profile": config.environment_profile_config["id"],
            "measurement_protocol": config.measurement_protocol_config["id"],
            "load_profile": config.load_profile_config["id"],
            "build_profile": config.build_profile_config["id"],
        },
        "contracts": contracts,
        "assets": assets,
        "execution": {
            "runtime": runtime,
            "target": config.target,
            "services": services,
            "load": config.load,
            "startup": config.startup,
            "compose_profile": config.compose_profile,
            "image_tag": config.image_tag,
        },
        "cohort": cohort,
    }
    manifest_payload = _canonical_copy(manifest_payload)
    manifest = {
        **manifest_payload,
        "manifest_digest": _canonical_digest(manifest_payload),
    }
    return _canonical_copy(manifest)


def validate_resolved_manifest(manifest: object, root: Path) -> None:
    schema_path = root / "contracts/schemas/resolved-run-manifest.schema.json"
    schema = json.loads(schema_path.read_text())
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
    assets = manifest["assets"]
    assert isinstance(cohort, dict)
    assert isinstance(contracts, dict)
    assert isinstance(assets, list)

    cohort_contracts = cohort["contracts"]
    assert isinstance(cohort_contracts, dict)
    for role in _COHORT_CONTRACT_ROLES:
        if cohort_contracts[role] != contracts[role]:
            validation_errors.append(
                f"$.cohort.contracts.{role}: must match $.contracts.{role}"
            )

    expected_assets = [
        asset for asset in assets if asset["role"] in _COHORT_ASSET_ROLES
    ]
    if cohort["assets"] != expected_assets:
        validation_errors.append(
            "$.cohort.assets: must match the comparable projection of $.assets"
        )

    cohort_payload = {
        key: value for key, value in cohort.items() if key != "fingerprint"
    }
    expected_fingerprint = _canonical_digest(cohort_payload)
    if cohort["fingerprint"] != expected_fingerprint:
        validation_errors.append(
            "$.cohort.fingerprint: does not match the cohort payload"
        )

    manifest_payload = {
        key: value for key, value in manifest.items() if key != "manifest_digest"
    }
    expected_manifest_digest = _canonical_digest(manifest_payload)
    if manifest["manifest_digest"] != expected_manifest_digest:
        validation_errors.append(
            "$.manifest_digest: does not match the manifest payload"
        )

    if validation_errors:
        raise ManifestValidationError(validation_errors)

    selection = manifest["selection"]
    assert isinstance(selection, dict)
    try:
        config = resolve_run_config(
            str(selection["implementation"]),
            str(selection["scenario"]),
            str(selection["variant"]),
            root,
            load_profile=str(selection["load_profile"]),
            environment_profile=str(selection["environment_profile"]),
            measurement_protocol=str(selection["measurement_protocol"]),
            build_profile=str(selection["build_profile"]),
        )
    except ValueError as error:
        raise ManifestValidationError([f"$.selection: {error}"]) from error

    expected = build_resolved_manifest(
        config,
        str(manifest["run_id"]),
        read_git_provenance(root),
    )
    mismatches = _resolved_manifest_mismatches(manifest, expected)
    if mismatches:
        raise ManifestValidationError(mismatches)


def _update_snapshot(
    digest: "hashlib._Hash",
    relative_path: str,
    file_type: str,
    mode: str,
    content: bytes,
) -> None:
    for part in (
        relative_path.encode(),
        file_type.encode(),
        mode.encode(),
        content,
    ):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)


def _asset_ref(
    role: str,
    path: Path,
    relative_path: str,
) -> dict[str, str]:
    return {
        "role": role,
        "path": relative_path,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _repository_path_without_symlinks(
    path: Path,
    root: Path,
    location: str,
) -> str:
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
            [f"{location}: symlink components are not allowed: {relative_path.as_posix()}"]
        )
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            raise ManifestValidationError(
                [f"{location}: symlink components are not allowed: {relative_path.as_posix()}"]
            )
    return relative_path.as_posix()


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
                if key == "path" and isinstance(child, str):
                    if not _is_canonical_repository_path(child):
                        errors.append(
                            f"{child_location}: must be a normalized repository-relative POSIX path"
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
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return location
