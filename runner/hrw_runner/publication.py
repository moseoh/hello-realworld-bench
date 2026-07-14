from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .build_evidence import validate_build_publication_evidence
from .evidence import (
    sha256_file,
    validate_lifecycle_publication_evidence,
    validate_run_set_evidence,
)
from .manifest import validate_resolved_manifest


_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_OFFICIAL_SERVICE_SCENARIOS = {
    "transactional-command-api",
    "io-aggregation-api",
    "read-heavy-query-api",
}
_OFFICIAL_SERVICE_LOAD_PROFILES = {"steady", "capacity-ramp", "burst-recovery"}
_PUBLICATION_IDENTITY_FIELDS = {
    "evidence_family",
    "selection",
    "started_at",
    "finished_at",
}


class PublicationError(ValueError):
    pass


def publish_run_set(
    run_set_dir: Path,
    dataset_dir: Path,
    root_dir: Path,
    *,
    source_commit: str,
    workflow_url: str | None = None,
    raw_artifact_url: str | None = None,
    raw_artifact_sha256: str | None = None,
) -> Path:
    run_set_dir = run_set_dir.resolve()
    dataset_dir.parent.mkdir(parents=True, exist_ok=True)
    manifest, run_set, family = _load_and_validate_evidence(run_set_dir, root_dir)
    _validate_promotion(run_set, manifest, source_commit)
    if family == "lifecycle":
        validate_lifecycle_publication_evidence(run_set_dir, root_dir)
    if bool(raw_artifact_url) != bool(raw_artifact_sha256):
        raise PublicationError(
            "Raw artifact URL and SHA-256 must be provided together"
        )
    if raw_artifact_sha256 and not re.fullmatch(r"[0-9a-f]{64}", raw_artifact_sha256):
        raise PublicationError("Raw artifact SHA-256 is invalid")

    run_set_id = _safe_id(run_set["run_set_id"], "run_set_id")
    cohort = _safe_id(run_set["cohort_fingerprint"], "cohort_fingerprint")
    relative_entry = _entry_path(family, cohort, run_set_id)
    entry_dir = dataset_dir / relative_entry
    selected_files = _publication_files(run_set_dir, run_set, family)
    started_at = _run_set_started_at(run_set_dir, run_set, family)
    finished_at = _run_set_finished_at(run_set_dir, run_set, family)

    with tempfile.TemporaryDirectory(dir=dataset_dir.parent) as temp_dir:
        staged = Path(temp_dir) / "entry"
        staged.mkdir(parents=True)
        published_files = []
        for relative_path in selected_files:
            source = run_set_dir / relative_path
            destination = staged / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            published_files.append(
                {
                    "path": relative_path.as_posix(),
                    "size_bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            )

        publication = {
            "schema_version": "1.0",
            "run_set_id": run_set_id,
            "cohort_fingerprint": cohort,
            "source_commit": source_commit,
            "workflow_url": workflow_url,
            "raw_artifact_url": raw_artifact_url,
            "raw_artifact_sha256": raw_artifact_sha256,
            "evidence_family": family,
            "selection": manifest["selection"],
            "started_at": started_at,
            "finished_at": finished_at,
            "files": published_files,
        }
        if family != "build":
            publication["image_digest"] = _image_digest(
                _read_object(run_set_dir / "build.json")
            )
        _write_json(staged / "publication.json", publication)

        if entry_dir.exists():
            _verify_existing_entry(entry_dir, publication)
        else:
            entry_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staged, entry_dir)

    catalog_entry = {
        "run_set_id": run_set_id,
        "cohort_fingerprint": cohort,
        "path": relative_entry.as_posix(),
        "publication_sha256": sha256_file(entry_dir / "publication.json"),
        "source_commit": source_commit,
        "started_at": started_at,
        "finished_at": finished_at,
        "evidence_family": family,
        "selection": manifest["selection"],
    }
    if family != "build":
        catalog_entry["image_digest"] = publication["image_digest"]
    _update_catalog(dataset_dir, catalog_entry)
    return entry_dir


def _load_and_validate_evidence(
    run_set_dir: Path, root_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], str]:
    build_manifest_path = run_set_dir / "build-resolved-manifest.json"
    standard_manifest_path = run_set_dir / "resolved-manifest.json"
    if build_manifest_path.is_file():
        if standard_manifest_path.exists():
            raise PublicationError("Run set contains multiple evidence manifest families")
        manifest = _read_object(build_manifest_path)
        family = manifest.get("cohort", {}).get("evidence_family")
        if family != "build":
            raise PublicationError("Build evidence manifest has an invalid family")
        validate_build_publication_evidence(run_set_dir, root_dir)
        return manifest, _read_object(run_set_dir / "build-run-set.json"), family

    manifest = _read_object(standard_manifest_path)
    validate_resolved_manifest(manifest, root_dir)
    validate_run_set_evidence(run_set_dir, root_dir)
    family = manifest.get("cohort", {}).get("evidence_family")
    if family not in {"service", "lifecycle"}:
        raise PublicationError("Run set has an unsupported evidence family")
    return manifest, _read_object(run_set_dir / "run-set.json"), family


def _validate_promotion(
    run_set: dict[str, Any], manifest: dict[str, Any], source_commit: str
) -> None:
    if not _COMMIT_PATTERN.fullmatch(source_commit):
        raise PublicationError("source_commit must be a full lowercase Git commit")
    if run_set.get("status") != "complete":
        raise PublicationError("Only complete run sets can be published")
    trials = run_set.get("trials", [])
    expected = run_set.get("expected_trials")
    if not trials or expected != len(trials):
        raise PublicationError("A published run set must contain every expected trial")
    if any(trial.get("status") != "valid" for trial in trials):
        raise PublicationError("Every published trial must be valid")
    summary = run_set.get("summary", {})
    if summary.get("valid_trial_count") != expected:
        raise PublicationError("Run set valid trial count is incomplete")

    source = manifest.get("source", {})
    if source.get("git_dirty") is not False:
        raise PublicationError("Dirty source results cannot be published")
    if source.get("git_commit") != source_commit:
        raise PublicationError("Trusted source commit does not match the run manifest")
    selection = manifest.get("selection", {})
    cohort = manifest.get("cohort", {})
    family = cohort.get("evidence_family") if isinstance(cohort, dict) else None
    combination = (
        selection.get("scenario"),
        selection.get("load_profile"),
        selection.get("environment_profile"),
        selection.get("measurement_protocol"),
    )
    service_allowed = (
        family == "service"
        and combination[2:] == ("home-k3s-v1", "official-service-v1")
        and (
            combination[:2] == ("ping-api", "platform-qualification-v1")
            or (
                combination[0] in _OFFICIAL_SERVICE_SCENARIOS
                and combination[1] in _OFFICIAL_SERVICE_LOAD_PROFILES
            )
        )
    )
    lifecycle_allowed = family == "lifecycle" and combination == (
        "cold-start-api",
        "none",
        "home-k3s-lifecycle-v1",
        "official-cold-start-v1",
    )
    build_allowed = family == "build" and (
        selection.get("environment_profile"),
        selection.get("measurement_protocol"),
        selection.get("build_profile"),
    ) == (
        "home-build-v1",
        "official-build-v1",
        "official-gradle-docker-v1",
    )
    if not (service_allowed or lifecycle_allowed or build_allowed):
        raise PublicationError("Run set is not an allowlisted official evidence cohort")


def _publication_files(
    run_set_dir: Path, run_set: dict[str, Any], family: str
) -> list[Path]:
    if family == "build":
        return _build_publication_files(run_set_dir, run_set)
    paths = {
        Path("run-set.json"),
        Path("resolved-manifest.json"),
    }
    for reference in run_set.get("platform_evidence", {}).values():
        paths.add(_contained_relative_path(run_set_dir, reference["path"]))
    for trial_reference in run_set["trials"]:
        trial_path = _contained_relative_path(run_set_dir, trial_reference["path"])
        paths.add(trial_path)
        trial = _read_object(run_set_dir / trial_path)
        trial_dir = trial_path.parent
        paths.add(
            _contained_relative_path(
                run_set_dir, (trial_dir / trial["time_series"]["path"]).as_posix()
            )
        )
        paths.add(
            _contained_relative_path(
                run_set_dir,
                (trial_dir / trial["artifact_manifest"]["path"]).as_posix(),
            )
        )
        result_path = trial_dir / "result.json"
        if not (run_set_dir / result_path).is_file():
            raise PublicationError(f"Missing normalized trial result: {result_path}")
        paths.add(result_path)
    return sorted(paths, key=lambda path: path.as_posix())


def _build_publication_files(run_set_dir: Path, run_set: dict[str, Any]) -> list[Path]:
    paths = {
        Path("build-run-set.json"),
        Path("build-resolved-manifest.json"),
    }
    campaign_evidence = run_set.get("campaign_evidence")
    if not isinstance(campaign_evidence, dict):
        raise PublicationError("Build run set has no campaign evidence")
    for name in ("preflight", "postflight", "cache_seed"):
        reference = campaign_evidence.get(name)
        if not isinstance(reference, dict):
            raise PublicationError(f"Build run set has no {name} evidence")
        paths.add(_contained_relative_path(run_set_dir, reference.get("path")))
    for trial_reference in run_set["trials"]:
        trial_path = _contained_relative_path(run_set_dir, trial_reference["path"])
        paths.add(trial_path)
        trial = _read_object(run_set_dir / trial_path)
        artifact_manifest = trial.get("artifact_manifest")
        if not isinstance(artifact_manifest, dict):
            raise PublicationError("Build trial has no artifact manifest")
        paths.add(
            _contained_relative_path(
                run_set_dir,
                (trial_path.parent / str(artifact_manifest.get("path", ""))).as_posix(),
            )
        )
    return sorted(paths, key=lambda path: path.as_posix())


def _entry_path(family: str, cohort: str, run_set_id: str) -> Path:
    root = "build-run-sets" if family == "build" else "run-sets"
    return Path(root) / cohort / run_set_id


def _run_set_started_at(
    run_set_dir: Path, run_set: dict[str, Any], family: str
) -> str:
    if family != "build":
        return str(run_set["started_at"])
    return _build_trial_times(run_set_dir, run_set)[0]


def _run_set_finished_at(
    run_set_dir: Path, run_set: dict[str, Any], family: str
) -> str:
    if family != "build":
        return str(run_set["finished_at"])
    return _build_trial_times(run_set_dir, run_set)[1]


def _build_trial_times(run_set_dir: Path, run_set: dict[str, Any]) -> tuple[str, str]:
    times = []
    for reference in run_set["trials"]:
        trial_path = _contained_relative_path(run_set_dir, reference["path"])
        trial = _read_object(run_set_dir / trial_path)
        started_at = trial.get("started_at")
        finished_at = trial.get("finished_at")
        if not isinstance(started_at, str) or not isinstance(finished_at, str):
            raise PublicationError("Build trial timestamps are invalid")
        times.append((started_at, finished_at))
    if not times:
        raise PublicationError("Build run set has no trials")
    return min(started_at for started_at, _ in times), max(
        finished_at for _, finished_at in times
    )


def _verify_existing_entry(entry_dir: Path, expected: dict[str, Any]) -> None:
    publication_path = entry_dir / "publication.json"
    if not publication_path.is_file():
        raise PublicationError("Existing append-only entry has no publication manifest")
    existing = _read_object(publication_path)
    if existing != expected and not _legacy_publication_matches(existing, expected):
        raise PublicationError("Existing append-only entry conflicts with this publication")
    for file_entry in existing.get("files", []):
        path = entry_dir / file_entry["path"]
        if (
            not path.is_file()
            or path.stat().st_size != file_entry["size_bytes"]
            or sha256_file(path) != file_entry["sha256"]
        ):
            raise PublicationError("Existing append-only entry content has changed")


def _update_catalog(dataset_dir: Path, entry: dict[str, Any]) -> None:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = dataset_dir / "catalog.json"
    if catalog_path.exists():
        catalog = _read_object(catalog_path)
        if catalog.get("schema_version") != "1.0" or not isinstance(
            catalog.get("entries"), list
        ):
            raise PublicationError("Dataset catalog has an unsupported shape")
    else:
        catalog = {"schema_version": "1.0", "entries": []}

    _validate_catalog_entries(dataset_dir, catalog)

    matches = [item for item in catalog["entries"] if item.get("run_set_id") == entry["run_set_id"]]
    if matches and not _catalog_entries_match(matches[0], entry):
        raise PublicationError("Dataset catalog contains a conflicting append-only entry")
    if not matches:
        catalog["entries"].append(entry)
    catalog["entries"].sort(key=lambda item: (item["finished_at"], item["run_set_id"]))
    _write_json(catalog_path, catalog)


def _validate_catalog_entries(dataset_dir: Path, catalog: dict[str, Any]) -> None:
    run_set_ids: set[str] = set()
    entry_paths: set[str] = set()
    for entry in catalog["entries"]:
        if not isinstance(entry, dict):
            raise PublicationError("Dataset catalog entries must be objects")
        run_set_id = _safe_id(entry.get("run_set_id"), "catalog run_set_id")
        cohort = _safe_id(
            entry.get("cohort_fingerprint"), "catalog cohort_fingerprint"
        )
        family = entry.get("evidence_family")
        if family not in {None, "service", "lifecycle", "build"}:
            raise PublicationError("Dataset catalog evidence family is invalid")
        expected_path = _entry_path(
            "build" if family == "build" else "service",
            cohort,
            run_set_id,
        ).as_posix()
        if entry.get("path") != expected_path:
            raise PublicationError("Dataset catalog entry path does not match its identity")
        if run_set_id in run_set_ids or expected_path in entry_paths:
            raise PublicationError("Dataset catalog identities and paths must be unique")
        run_set_ids.add(run_set_id)
        entry_paths.add(expected_path)

        entry_dir = dataset_dir / expected_path
        publication_path = entry_dir / "publication.json"
        expected_digest = entry.get("publication_sha256")
        if (
            not publication_path.is_file()
            or not isinstance(expected_digest, str)
            or sha256_file(publication_path) != expected_digest
        ):
            raise PublicationError("Existing catalog publication digest is invalid")
        publication = _read_object(publication_path)
        _verify_existing_entry(entry_dir, publication)
        compact_identity = _compact_evidence_identity(entry_dir)
        if family is None:
            if not _is_official_legacy_service_entry(
                entry,
                publication,
                compact_identity,
            ):
                raise PublicationError(
                    "Dataset catalog legacy entry is not an official service shape"
                )
            effective_family = "service"
        else:
            effective_family = family
            _validate_modern_catalog_identity(
                entry,
                publication,
                compact_identity,
            )
        if effective_family == "build":
            if "image_digest" in entry or "image_digest" in publication:
                raise PublicationError("Build catalog entries cannot have image digests")
        else:
            catalog_image_digest = entry.get("image_digest")
            publication_image_digest = publication.get("image_digest")
            if (
                not isinstance(catalog_image_digest, str)
                or not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", catalog_image_digest
                )
                or not isinstance(publication_image_digest, str)
                or not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", publication_image_digest
                )
                or catalog_image_digest != publication_image_digest
            ):
                raise PublicationError("Existing catalog image digest is invalid")
        if (
            publication.get("run_set_id") != run_set_id
            or publication.get("cohort_fingerprint") != cohort
            or publication.get("source_commit") != entry.get("source_commit")
        ):
            raise PublicationError("Existing catalog publication identity is invalid")


def _compact_evidence_identity(entry_dir: Path) -> dict[str, Any]:
    build_manifest_path = entry_dir / "build-resolved-manifest.json"
    if build_manifest_path.is_file():
        manifest = _read_object(build_manifest_path)
        run_set = _read_object(entry_dir / "build-run-set.json")
        family = "build"
    else:
        manifest = _read_object(entry_dir / "resolved-manifest.json")
        run_set = _read_object(entry_dir / "run-set.json")
        cohort = manifest.get("cohort")
        family = cohort.get("evidence_family") if isinstance(cohort, dict) else None
        if family not in {"service", "lifecycle"}:
            raise PublicationError("Existing compact evidence family is invalid")
    cohort = manifest.get("cohort")
    source = manifest.get("source")
    if not isinstance(cohort, dict) or not isinstance(source, dict):
        raise PublicationError("Existing compact evidence identity is invalid")
    return {
        "run_set_id": run_set.get("run_set_id"),
        "cohort_fingerprint": cohort.get("fingerprint"),
        "source_commit": source.get("git_commit"),
        "started_at": _run_set_started_at(entry_dir, run_set, family),
        "finished_at": _run_set_finished_at(entry_dir, run_set, family),
        "evidence_family": family,
        "selection": manifest.get("selection"),
    }


def _validate_modern_catalog_identity(
    entry: dict[str, Any],
    publication: dict[str, Any],
    compact_identity: dict[str, Any],
) -> None:
    publication_identity = {
        field: publication.get(field)
        for field in compact_identity
    }
    catalog_identity = {
        field: entry.get(field)
        for field in compact_identity
    }
    if publication_identity != compact_identity or catalog_identity != compact_identity:
        raise PublicationError(
            "Dataset catalog does not match compact evidence identity"
        )


def _is_official_legacy_service_entry(
    entry: dict[str, Any],
    publication: dict[str, Any],
    compact_identity: dict[str, Any],
) -> bool:
    expected_catalog_fields = {
        "run_set_id",
        "cohort_fingerprint",
        "path",
        "publication_sha256",
        "source_commit",
        "started_at",
        "finished_at",
        "selection",
        "image_digest",
    }
    expected_publication_fields = {
        "schema_version",
        "run_set_id",
        "cohort_fingerprint",
        "source_commit",
        "workflow_url",
        "raw_artifact_url",
        "raw_artifact_sha256",
        "files",
        "image_digest",
    }
    if (
        set(entry) != expected_catalog_fields
        or set(publication) != expected_publication_fields
        or compact_identity.get("evidence_family") != "service"
        or not _official_service_selection(compact_identity.get("selection"))
    ):
        return False
    for field in (
        "run_set_id",
        "cohort_fingerprint",
        "source_commit",
        "started_at",
        "finished_at",
        "selection",
    ):
        if entry.get(field) != compact_identity.get(field):
            return False
    return all(
        publication.get(field) == compact_identity.get(field)
        for field in ("run_set_id", "cohort_fingerprint", "source_commit")
    )


def _official_service_selection(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    scenario = value.get("scenario")
    load_profile = value.get("load_profile")
    if (
        value.get("environment_profile") != "home-k3s-v1"
        or value.get("measurement_protocol") != "official-service-v1"
    ):
        return False
    return (
        scenario == "ping-api" and load_profile == "platform-qualification-v1"
    ) or (
        scenario in _OFFICIAL_SERVICE_SCENARIOS
        and load_profile in _OFFICIAL_SERVICE_LOAD_PROFILES
    )


def _legacy_publication_matches(
    existing: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    if (
        expected.get("evidence_family") != "service"
        or not _official_service_selection(expected.get("selection"))
        or any(field in existing for field in _PUBLICATION_IDENTITY_FIELDS)
    ):
        return False
    return existing == {
        key: value
        for key, value in expected.items()
        if key not in _PUBLICATION_IDENTITY_FIELDS
    }


def _catalog_entries_match(existing: dict[str, Any], entry: dict[str, Any]) -> bool:
    if existing == entry:
        return True
    if (
        existing.get("evidence_family") is None
        and entry.get("evidence_family") == "service"
    ):
        return existing == {
            key: value for key, value in entry.items() if key != "evidence_family"
        }
    return False


def _image_digest(build: dict[str, Any]) -> str:
    digest = build.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise PublicationError("Published build evidence must contain an image digest")
    return digest


def _safe_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise PublicationError(f"Invalid {name}")
    return value


def _contained_relative_path(root: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise PublicationError("Evidence path must be a string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PublicationError(f"Evidence path escapes the run set: {value}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise PublicationError(f"Evidence file is missing: {value}")
    return path


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise PublicationError(f"Expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
