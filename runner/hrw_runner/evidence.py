from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from statistics import median
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .kubernetes_lifecycle import (
    build_lifecycle_measurement,
    build_prepull_evidence,
    evaluate_lifecycle_boundaries,
    validate_lifecycle_pod,
)
from .kubernetes_stats import normalize_stats_sample


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
        (
            "dependency_ready_ms",
            "ready_ms",
            "entrypoint_pre_exec_to_first_valid_response_ms",
            "first_request_ms",
        ),
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


def build_trial_summary(
    result: dict[str, Any],
    resource_source: str = "docker-stats.json",
) -> list[dict[str, object]]:
    sources = {
        "rps": ("requests_per_second", "k6-summary.json"),
        "p50_ms": ("milliseconds", "k6-summary.json"),
        "p95_ms": ("milliseconds", "k6-summary.json"),
        "p99_ms": ("milliseconds", "k6-summary.json"),
        "error_rate": ("ratio", "k6-summary.json"),
        "cpu_percent_avg": ("percent", resource_source),
        "cpu_percent_max": ("percent", resource_source),
        "memory_usage_max_bytes": ("bytes", resource_source),
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
    for name in (
        "dependency_ready_ms",
        "ready_ms",
        "entrypoint_pre_exec_to_first_valid_response_ms",
        "first_request_ms",
    ):
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
    selection = manifest.get("selection", {})
    family = cohort.get("evidence_family")
    environment_profile = selection.get("environment_profile")
    if environment_profile in {"home-k3s-v1", "home-k3s-lifecycle-v1"}:
        required_platform = {"preflight", "postflight", "build"}
        if family == "lifecycle":
            required_platform.add("image_prepull")
        platform = run_set.get("platform_evidence")
        if not isinstance(platform, dict):
            raise ValueError("Official k3s run set is missing platform evidence")
        missing = sorted(required_platform - platform.keys())
        if missing:
            raise ValueError(
                "Official k3s run set is missing platform evidence: "
                + ", ".join(missing)
            )
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
    for reference in run_set.get("platform_evidence", {}).values():
        path = _contained_file(run_set_dir, str(reference["path"]))
        if sha256_file(path) != reference["sha256"]:
            raise ValueError(f"Platform evidence digest mismatch: {reference['path']}")
    if family == "lifecycle":
        validate_lifecycle_publication_evidence(
            run_set_dir,
            root_dir,
            run_set=run_set,
            manifest=manifest,
        )


def validate_lifecycle_publication_evidence(
    run_set_dir: Path,
    root_dir: Path,
    *,
    run_set: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> None:
    run_set = run_set or _read_object(run_set_dir / "run-set.json")
    manifest = manifest or _read_object(run_set_dir / "resolved-manifest.json")
    if manifest.get("cohort", {}).get("evidence_family") != "lifecycle":
        return
    if (
        manifest.get("selection", {}).get("environment_profile")
        != "home-k3s-lifecycle-v1"
    ):
        return

    environment_ref = manifest.get("contracts", {}).get("environment_profile", {})
    relative_contract = environment_ref.get("path")
    if not isinstance(relative_contract, str):
        raise ValueError("Lifecycle manifest has no environment contract path")
    contract_path = _contained_repository_file(root_dir, relative_contract)
    environment = yaml.safe_load(contract_path.read_text())
    if not isinstance(environment, dict):
        raise ValueError("Lifecycle environment contract is not an object")
    observer_image = str(environment.get("images", {}).get("k6", ""))
    target_image = str(manifest.get("execution", {}).get("image_tag", ""))
    timeout_seconds = int(
        manifest.get("execution", {}).get("startup", {}).get("timeout_seconds", 0)
    )
    if timeout_seconds < 1:
        raise ValueError("Lifecycle manifest has no positive timeout")

    platform = run_set.get("platform_evidence", {})
    prepull_ref = platform.get("image_prepull")
    if not isinstance(prepull_ref, dict):
        raise ValueError("Lifecycle run set has no image pre-pull evidence")
    prepull_path = _contained_file(run_set_dir, str(prepull_ref.get("path", "")))
    prepull = _read_object(prepull_path)
    pod = prepull.get("pod")
    if not isinstance(pod, dict):
        raise ValueError("Lifecycle image pre-pull evidence has no Pod")
    expected_prepull = build_prepull_evidence(
        pod,
        target_image=target_image,
        observer_image=observer_image,
    )
    if prepull != expected_prepull or prepull.get("status") != "valid":
        raise ValueError("Lifecycle image pre-pull evidence is invalid")

    validated_trials = []
    for reference in run_set["trials"]:
        if reference.get("status") != "valid":
            continue
        trial_path = _contained_file(run_set_dir, str(reference["path"]))
        trial_dir = trial_path.parent
        required = {
            "startup.json",
            "target-pod.json",
            "target.log",
            "observer.log",
            "boundary-validity.json",
            "boundary-kubelet-stats.json",
            "result.json",
        }
        artifact_manifest = _read_object(trial_dir / "artifact-manifest.json")
        recorded = {
            str(artifact.get("path"))
            for artifact in artifact_manifest.get("artifacts", [])
        }
        missing = sorted(required - recorded)
        if missing:
            raise ValueError(
                f"Lifecycle trial {reference['trial_id']} is missing raw evidence: "
                + ", ".join(missing)
            )

        pod = _read_object(trial_dir / "target-pod.json")
        expected_startup = build_lifecycle_measurement(
            pod,
            (trial_dir / "target.log").read_text(),
            (trial_dir / "observer.log").read_text(),
            timeout_seconds=timeout_seconds,
        )
        if _read_object(trial_dir / "startup.json") != expected_startup:
            raise ValueError(
                f"Lifecycle trial {reference['trial_id']} startup evidence is inconsistent"
            )
        result = _read_object(trial_dir / "result.json")
        if result.get("startup") != expected_startup:
            raise ValueError(
                f"Lifecycle trial {reference['trial_id']} result startup is inconsistent"
            )
        pod_reasons = validate_lifecycle_pod(
            pod,
            target_image=target_image,
            observer_image=observer_image,
        )
        if pod_reasons:
            raise ValueError(
                f"Lifecycle trial {reference['trial_id']} Pod evidence is invalid: "
                + "; ".join(pod_reasons)
            )

        namespace = pod.get("metadata", {}).get("namespace")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("Lifecycle Pod evidence has no namespace")
        raw_stats = _read_object(trial_dir / "boundary-kubelet-stats.json")
        if not isinstance(raw_stats.get("before"), dict) or not isinstance(
            raw_stats.get("after"), dict
        ):
            raise ValueError("Lifecycle boundary raw evidence is incomplete")
        expected_boundary = evaluate_lifecycle_boundaries(
            normalize_stats_sample(raw_stats["before"], namespace, 0),
            normalize_stats_sample(raw_stats["after"], namespace, 0),
            environment["validity"],
        )
        boundary = _read_object(trial_dir / "boundary-validity.json")
        if boundary != expected_boundary or boundary.get("status") != "valid":
            raise ValueError(
                f"Lifecycle trial {reference['trial_id']} boundary evidence is invalid"
            )
        trial = _read_object(trial_path)
        expected_trial_summary = build_trial_summary(result, "target-pod.json")
        if trial.get("summary") != expected_trial_summary:
            raise ValueError(
                f"Lifecycle trial {reference['trial_id']} summary is inconsistent"
            )
        validated_trials.append({**trial, "result": result})

    expected_summary = summarize_trials(validated_trials)
    if run_set.get("summary") != expected_summary:
        raise ValueError("Lifecycle run-set summary is inconsistent with raw evidence")


def _contained_repository_file(root_dir: Path, relative_path: str) -> Path:
    root = root_dir.resolve(strict=True)
    path = (root / relative_path).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("Contract path escapes the repository") from None
    if not path.is_file():
        raise ValueError("Contract path is not a file")
    return path


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
