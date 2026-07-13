from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

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
    manifest = _read_object(run_set_dir / "resolved-manifest.json")
    validate_resolved_manifest(manifest, root_dir)
    validate_run_set_evidence(run_set_dir, root_dir)
    run_set = _read_object(run_set_dir / "run-set.json")
    _validate_promotion(run_set, manifest, source_commit)
    if manifest.get("cohort", {}).get("evidence_family") == "lifecycle":
        validate_lifecycle_publication_evidence(run_set_dir, root_dir)
    if bool(raw_artifact_url) != bool(raw_artifact_sha256):
        raise PublicationError(
            "Raw artifact URL and SHA-256 must be provided together"
        )
    if raw_artifact_sha256 and not re.fullmatch(r"[0-9a-f]{64}", raw_artifact_sha256):
        raise PublicationError("Raw artifact SHA-256 is invalid")

    run_set_id = _safe_id(run_set["run_set_id"], "run_set_id")
    cohort = _safe_id(run_set["cohort_fingerprint"], "cohort_fingerprint")
    relative_entry = Path("run-sets") / cohort / run_set_id
    entry_dir = dataset_dir / relative_entry
    selected_files = _publication_files(run_set_dir, run_set)

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

        build = _read_object(run_set_dir / "build.json")
        publication = {
            "schema_version": "1.0",
            "run_set_id": run_set_id,
            "cohort_fingerprint": cohort,
            "source_commit": source_commit,
            "image_digest": _image_digest(build),
            "workflow_url": workflow_url,
            "raw_artifact_url": raw_artifact_url,
            "raw_artifact_sha256": raw_artifact_sha256,
            "files": published_files,
        }
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
        "image_digest": publication["image_digest"],
        "started_at": run_set["started_at"],
        "finished_at": run_set["finished_at"],
        "evidence_family": manifest["cohort"]["evidence_family"],
        "selection": manifest["selection"],
    }
    _update_catalog(dataset_dir, catalog_entry)
    return entry_dir


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
    if not (service_allowed or lifecycle_allowed):
        raise PublicationError("Run set is not an allowlisted official evidence cohort")


def _publication_files(run_set_dir: Path, run_set: dict[str, Any]) -> list[Path]:
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


def _verify_existing_entry(entry_dir: Path, expected: dict[str, Any]) -> None:
    publication_path = entry_dir / "publication.json"
    if not publication_path.is_file():
        raise PublicationError("Existing append-only entry has no publication manifest")
    existing = _read_object(publication_path)
    if existing != expected:
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
    if matches and matches[0] != entry:
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
        expected_path = (Path("run-sets") / cohort / run_set_id).as_posix()
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
        if (
            publication.get("run_set_id") != run_set_id
            or publication.get("cohort_fingerprint") != cohort
            or publication.get("source_commit") != entry.get("source_commit")
            or publication.get("image_digest") != entry.get("image_digest")
        ):
            raise PublicationError("Existing catalog publication identity is invalid")
        _verify_existing_entry(entry_dir, publication)


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
