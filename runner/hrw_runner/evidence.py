from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from statistics import median
from typing import Any

from jsonschema import Draft202012Validator


_EVIDENCE_INDEX_FILES = {
    "artifact-manifest.json",
    "time-series.json",
    "trial.json",
}


def build_compact_time_series(
    trial_id: str,
    sample_interval_seconds: float,
    samples: list[dict[str, object]],
) -> dict[str, object]:
    normalized = []
    for sample in samples:
        elapsed_ms = sample.get("elapsed_ms")
        if not isinstance(elapsed_ms, int) or elapsed_ms < 0:
            continue
        normalized.append(
            {
                "elapsed_ms": elapsed_ms,
                "target_cpu_percent": _percent(sample.get("CPUPerc")),
                "target_memory_bytes": _memory_usage_bytes(sample.get("MemUsage")),
                "target_memory_percent": _percent(sample.get("MemPerc")),
            }
        )

    return {
        "schema_version": "1.0",
        "trial_id": trial_id,
        "sample_interval_ms": round(sample_interval_seconds * 1000),
        "samples": normalized,
    }


def build_artifact_manifest(trial_id: str, trial_dir: Path) -> dict[str, object]:
    artifacts = []
    for path in sorted(trial_dir.rglob("*")):
        if not path.is_file() or path.name in _EVIDENCE_INDEX_FILES:
            continue
        artifacts.append(
            {
                "path": path.relative_to(trial_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return {
        "schema_version": "1.0",
        "trial_id": trial_id,
        "artifacts": artifacts,
    }


def summarize_trials(trials: list[dict[str, Any]]) -> dict[str, object]:
    valid_trials = [trial for trial in trials if trial.get("status") == "valid"]
    runtime_metrics = _summarize_metric_group(
        valid_trials,
        "runtime_metrics",
        ("rps", "p50_ms", "p95_ms", "p99_ms", "error_rate"),
    )
    startup_metrics = _summarize_metric_group(
        valid_trials,
        "startup",
        ("dependency_ready_ms", "ready_ms", "first_request_ms"),
    )
    return {
        "trial_count": len(trials),
        "valid_trial_count": len(valid_trials),
        "runtime_metrics": runtime_metrics,
        "startup_metrics": startup_metrics,
    }


def _summarize_metric_group(
    valid_trials: list[dict[str, Any]],
    result_group: str,
    metric_names: tuple[str, ...],
) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for metric_name in metric_names:
        values = []
        for trial in valid_trials:
            value = trial.get("result", {}).get(result_group, {}).get(metric_name)
            if isinstance(value, (int, float)):
                values.append(float(value))
        if values:
            trial_values = [
                {
                    "trial_id": str(trial["trial_id"]),
                    "value": float(
                        trial.get("result", {})
                        .get(result_group, {})
                        .get(metric_name)
                    ),
                }
                for trial in valid_trials
                if isinstance(
                    trial.get("result", {})
                    .get(result_group, {})
                    .get(metric_name),
                    (int, float),
                )
            ]
            metrics[metric_name] = {
                "min": min(values),
                "median": median(values),
                "max": max(values),
                "trials": trial_values,
            }
    return metrics


def build_trial_summary(result: dict[str, Any]) -> list[dict[str, object]]:
    sources = {
        "rps": ("requests_per_second", "k6-summary.json"),
        "p50_ms": ("milliseconds", "k6-summary.json"),
        "p95_ms": ("milliseconds", "k6-summary.json"),
        "p99_ms": ("milliseconds", "k6-summary.json"),
        "error_rate": ("ratio", "k6-summary.json"),
        "cpu_percent_avg": ("percent", "docker-stats.json"),
        "cpu_percent_max": ("percent", "docker-stats.json"),
        "memory_usage_max_bytes": ("bytes", "docker-stats.json"),
    }
    runtime_metrics = result.get("runtime_metrics", {})
    summary = [
        {
            "name": name,
            "unit": unit,
            "value": runtime_metrics.get(name),
            "source_artifacts": [source],
        }
        for name, (unit, source) in sources.items()
        if name in runtime_metrics
    ]
    startup = result.get("startup", {})
    for name in ("dependency_ready_ms", "ready_ms", "first_request_ms"):
        if name in startup:
            summary.append(
                {
                    "name": name,
                    "unit": "milliseconds",
                    "value": startup.get(name),
                    "source_artifacts": ["startup.json"],
                }
            )
    return summary


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_evidence_document(
    document: dict[str, object], schema_name: str, root_dir: Path
) -> None:
    schema = json.loads(
        (root_dir / "contracts" / "schemas" / f"{schema_name}.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{part!r}]" for part in error.absolute_path)
        raise ValueError(f"Invalid {schema_name} evidence at {location}: {error.message}")


def validate_run_set_evidence(run_set_dir: Path, root_dir: Path) -> None:
    run_set = _read_object(run_set_dir / "run-set.json")
    validate_evidence_document(run_set, "run-set", root_dir)
    manifest = _read_object(run_set_dir / "resolved-manifest.json")
    if run_set["run_id"] != manifest.get("run_id"):
        raise ValueError("Run set run_id does not match resolved manifest")
    if run_set["manifest_digest"] != manifest.get("manifest_digest"):
        raise ValueError("Run set manifest digest does not match resolved manifest")
    cohort = manifest.get("cohort", {})
    if run_set["cohort_fingerprint"] != cohort.get("fingerprint"):
        raise ValueError("Run set cohort fingerprint does not match resolved manifest")
    references = run_set["trials"]
    if run_set["expected_trials"] != len(references):
        raise ValueError("Run set expected_trials does not match trial references")
    if run_set["summary"]["trial_count"] != len(references):
        raise ValueError("Run set summary trial_count does not match trial references")
    for field in ("trial_id", "index", "path"):
        values = [reference[field] for reference in references]
        if len(values) != len(set(values)):
            raise ValueError(f"Run set trial {field} values must be unique")

    for reference in references:
        trial_path = _contained_file(run_set_dir, str(reference["path"]))
        if sha256_file(trial_path) != reference["sha256"]:
            raise ValueError(f"Trial digest mismatch: {reference['trial_id']}")
        trial = _read_object(trial_path)
        validate_evidence_document(trial, "trial", root_dir)
        _require_equal_identity(run_set, trial, reference)
        trial_dir = trial_path.parent
        time_series = _validated_reference(
            trial_dir, trial["time_series"], "time-series", root_dir
        )
        artifact_manifest = _validated_reference(
            trial_dir, trial["artifact_manifest"], "artifact-manifest", root_dir
        )
        if time_series["trial_id"] != trial["trial_id"]:
            raise ValueError("Time-series trial_id does not match trial")
        if artifact_manifest["trial_id"] != trial["trial_id"]:
            raise ValueError("Artifact manifest trial_id does not match trial")
        elapsed = [sample["elapsed_ms"] for sample in time_series["samples"]]
        if elapsed != sorted(elapsed) or len(elapsed) != len(set(elapsed)):
            raise ValueError("Time-series elapsed_ms values must increase")
        artifact_paths = set()
        for artifact in artifact_manifest["artifacts"]:
            path = _contained_file(trial_dir, str(artifact["path"]))
            if artifact["path"] in artifact_paths:
                raise ValueError("Artifact manifest paths must be unique")
            artifact_paths.add(artifact["path"])
            if path.stat().st_size != artifact["size_bytes"]:
                raise ValueError(f"Artifact size mismatch: {artifact['path']}")
            if sha256_file(path) != artifact["sha256"]:
                raise ValueError(f"Artifact digest mismatch: {artifact['path']}")
        for metric in trial["summary"]:
            for source in metric["source_artifacts"]:
                if source not in artifact_paths:
                    raise ValueError(f"Summary source is not raw evidence: {source}")


def _validated_reference(
    directory: Path,
    reference: dict[str, Any],
    schema_name: str,
    root_dir: Path,
) -> dict[str, Any]:
    path = _contained_file(directory, str(reference["path"]))
    if sha256_file(path) != reference["sha256"]:
        raise ValueError(f"Evidence digest mismatch: {reference['path']}")
    document = _read_object(path)
    validate_evidence_document(document, schema_name, root_dir)
    return document


def _require_equal_identity(
    run_set: dict[str, Any], trial: dict[str, Any], reference: dict[str, Any]
) -> None:
    checks = (
        (trial["trial_id"], reference["trial_id"], "trial_id"),
        (trial["run_id"], run_set["run_id"], "run_id"),
        (trial["manifest_digest"], run_set["manifest_digest"], "manifest_digest"),
        (
            trial["cohort_fingerprint"],
            run_set["cohort_fingerprint"],
            "cohort_fingerprint",
        ),
        (trial["status"], reference["status"], "status"),
    )
    for actual, expected, field in checks:
        if actual != expected:
            raise ValueError(f"Trial {field} does not match run set")


def _contained_file(directory: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError(f"Invalid evidence path: {relative_path}")
    base = directory.resolve(strict=True)
    path = base / relative
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(base)
    except (FileNotFoundError, ValueError):
        raise ValueError(f"Invalid evidence path: {relative_path}") from None
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"Invalid evidence path: {relative_path}")
    if resolved != path or not path.is_file():
        raise ValueError(f"Invalid evidence path: {relative_path}")
    return path


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _percent(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return float(value.strip().removesuffix("%"))
    except ValueError:
        return None


def _memory_usage_bytes(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    used = value.split("/", 1)[0].strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]+)?", used)
    if not match:
        return None
    multipliers = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
    }
    multiplier = multipliers.get((match.group(2) or "B").lower())
    if multiplier is None:
        return None
    return round(float(match.group(1)) * multiplier)
