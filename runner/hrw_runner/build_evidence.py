from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import tarfile
from pathlib import Path
from pathlib import PurePosixPath
from statistics import median
from typing import Any

from jsonschema import Draft202012Validator

from .build_manifest import validate_resolved_build_manifest


_OPERATION_METRICS = (
    ("gradle_clean_build", "gradle_clean_build_ms"),
    ("image_package", "image_package_ms"),
    ("gradle_incremental_rebuild", "gradle_incremental_rebuild_ms"),
    ("image_rebuild", "image_rebuild_ms"),
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OCI_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GRADLE_EXECUTOR_IMAGE = (
    "eclipse-temurin:25-jdk@sha256:"
    "68868d04fa9cfd5f5c6abec0b5cef86d8de2bf9c62c37c7d3e4f0f80f5cfd7ff"
)
_BUILDKIT_IMAGE = (
    "moby/buildkit:buildx-stable-1@sha256:"
    "0168606be2315b7c807a03b3d8aa79beefdb31c98740cebdffdfeebf31190c9f"
)


def validate_build_document(
    document: dict[str, object], schema_name: str, root_dir: Path
) -> None:
    schema = json.loads(
        (root_dir / "contracts/schemas" / f"{schema_name}.schema.json").read_text()
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
        raise ValueError(
            f"Invalid {schema_name} evidence at {location}: {error.message}"
        )


def validate_build_publication_evidence(
    run_set_dir: Path,
    root_dir: Path,
    *,
    expected_implementation: str | None = None,
    expected_variant: str | None = None,
) -> list[str]:
    run_set = _read_object(run_set_dir / "build-run-set.json")
    validate_build_document(run_set, "build-run-set", root_dir)
    manifest = _read_object(run_set_dir / "build-resolved-manifest.json")
    validate_resolved_build_manifest(manifest, root_dir)
    _validate_manifest_identity(
        run_set,
        manifest,
        expected_implementation=expected_implementation,
        expected_variant=expected_variant,
    )

    references = _object_list(run_set.get("trials"), "Run set trials")
    if len(references) != 3 or run_set.get("expected_trials") != 3:
        raise ValueError("Build run set must contain exactly three trials")
    for field in ("trial_id", "index", "path"):
        values = [reference.get(field) for reference in references]
        if len(set(values)) != len(values):
            raise ValueError(f"Build trial {field} values must be unique")
    if any(reference.get("status") != "valid" for reference in references):
        raise ValueError("All build trial references must be valid")

    campaign_evidence = _object(
        run_set.get("campaign_evidence"), "Campaign evidence"
    )
    preflight = _validated_reference(run_set_dir, campaign_evidence.get("preflight"))
    postflight = _validated_reference(
        run_set_dir,
        campaign_evidence.get("postflight"),
    )
    cache_seed = _validated_reference(run_set_dir, campaign_evidence.get("cache_seed"))
    _validate_host_evidence(preflight, postflight)
    _validate_cache_seed(cache_seed, run_set_dir, root_dir, manifest)

    trials = []
    workspaces: set[str] = set()
    caches: set[str] = set()
    builders: set[str] = set()
    for expected_index, reference in enumerate(references, 1):
        trial_path = _contained_file(run_set_dir, str(reference.get("path", "")))
        if _sha256_file(trial_path) != reference.get("sha256"):
            raise ValueError(f"Trial digest mismatch: {reference.get('trial_id')}")
        trial = _read_object(trial_path)
        validate_build_document(trial, "build-trial", root_dir)
        _validate_trial_identity(run_set, trial, reference, expected_index)
        trial_inputs = _validate_trial_raw_evidence(
            trial_path.parent,
            trial,
            workspaces,
            caches,
            builders,
            cache_seed,
            manifest,
            root_dir,
        )
        if trial_inputs.get("builder_removed") is not True:
            raise ValueError("Buildx builder was not removed")
        if trial_inputs.get("state_volume_removed") is not True:
            raise ValueError("BuildKit state volume was not removed")
        trials.append(trial)

    if len(workspaces) != 3 or len(caches) != 3 or len(builders) != 3:
        raise ValueError("Each build trial must use fresh workspace, cache, and builder")
    expected_summary = summarize_build_trials(trials)
    if run_set.get("summary") != expected_summary:
        raise ValueError("Build run-set summary is inconsistent with raw evidence")
    return _validate_closed_run_set_files(run_set_dir, run_set, cache_seed)


def create_deterministic_build_archive(
    run_set_dir: Path,
    root_dir: Path,
    output_path: Path,
    *,
    expected_implementation: str | None = None,
    expected_variant: str | None = None,
) -> str:
    relative_paths = validate_build_publication_evidence(
        run_set_dir,
        root_dir,
        expected_implementation=expected_implementation,
        expected_variant=expected_variant,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as compressed:
        with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as gzip_file:
            with tarfile.open(
                fileobj=gzip_file,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                for relative_path in relative_paths:
                    path = run_set_dir / relative_path
                    info = tarfile.TarInfo(relative_path)
                    info.size = path.stat().st_size
                    info.mtime = 0
                    info.mode = 0o644
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as source:
                        archive.addfile(info, source)
    return _sha256_file(output_path)


def summarize_build_trials(trials: list[dict[str, Any]]) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for _, metric_name in _OPERATION_METRICS:
        trial_values = [
            {
                "trial_id": str(trial["trial_id"]),
                "value": float(_object(trial.get("metrics"), "Trial metrics")[metric_name]),
            }
            for trial in trials
        ]
        values = [entry["value"] for entry in trial_values]
        metrics[metric_name] = {
            "min": min(values),
            "median": median(values),
            "max": max(values),
            "trials": trial_values,
        }
    return {
        "trial_count": len(trials),
        "valid_trial_count": sum(trial.get("status") == "valid" for trial in trials),
        "build_metrics": metrics,
    }


def _validate_manifest_identity(
    run_set: dict[str, Any],
    manifest: dict[str, Any],
    *,
    expected_implementation: str | None,
    expected_variant: str | None,
) -> None:
    cohort = _object(manifest.get("cohort"), "Resolved build cohort")
    selection = _object(manifest.get("selection"), "Resolved build selection")
    if cohort.get("evidence_family") != "build":
        raise ValueError("Resolved manifest is not build evidence")
    expected_profiles = {
        "environment_profile": "home-build-v1",
        "measurement_protocol": "official-build-v1",
        "build_profile": "official-gradle-docker-v1",
    }
    for field, expected in expected_profiles.items():
        if selection.get(field) != expected:
            raise ValueError(f"Unsupported resolved build {field}")
    for field, expected in (
        ("implementation", expected_implementation),
        ("variant", expected_variant),
    ):
        if expected is not None and selection.get(field) != expected:
            raise ValueError(
                f"Resolved build selection does not match expected {field}"
            )
    if run_set.get("run_set_id") != run_set.get("run_id"):
        raise ValueError("Build run set run_set_id does not match run_id")
    checks = (
        (run_set.get("run_id"), manifest.get("run_id"), "run_id"),
        (
            run_set.get("manifest_digest"),
            manifest.get("manifest_digest"),
            "manifest digest",
        ),
        (
            run_set.get("cohort_fingerprint"),
            cohort.get("fingerprint"),
            "cohort fingerprint",
        ),
    )
    for actual, expected, field in checks:
        if actual != expected:
            raise ValueError(f"Build run set {field} does not match resolved manifest")


def _validate_trial_identity(
    run_set: dict[str, Any],
    trial: dict[str, Any],
    reference: dict[str, Any],
    expected_index: int,
) -> None:
    expected_trial_id = f"trial-{expected_index:02d}"
    checks = (
        (reference.get("index"), expected_index, "reference index"),
        (reference.get("trial_id"), expected_trial_id, "reference trial_id"),
        (trial.get("trial_id"), reference.get("trial_id"), "trial_id"),
        (trial.get("run_id"), run_set.get("run_id"), "run_id"),
        (
            trial.get("manifest_digest"),
            run_set.get("manifest_digest"),
            "manifest digest",
        ),
        (
            trial.get("cohort_fingerprint"),
            run_set.get("cohort_fingerprint"),
            "cohort fingerprint",
        ),
        (trial.get("status"), "valid", "status"),
    )
    for actual, expected, field in checks:
        if actual != expected:
            raise ValueError(f"Build trial {field} is invalid")


def _validate_trial_raw_evidence(
    trial_dir: Path,
    trial: dict[str, Any],
    workspaces: set[str],
    caches: set[str],
    builders: set[str],
    cache_seed: dict[str, Any],
    manifest: dict[str, Any],
    root_dir: Path,
) -> dict[str, Any]:
    operation_refs = _object_list(trial.get("operations"), "Build operations")
    if [reference.get("name") for reference in operation_refs] != [
        operation for operation, _ in _OPERATION_METRICS
    ]:
        raise ValueError("Build operation order is invalid")
    metrics = _object(trial.get("metrics"), "Build trial metrics")
    referenced_paths: set[str] = set()
    operation_records = []
    for reference, (operation_name, metric_name) in zip(
        operation_refs, _OPERATION_METRICS
    ):
        record = _validated_reference(trial_dir, reference)
        operation_records.append(record)
        referenced_paths.add(str(reference["path"]))
        log_path = _validate_operation_record(
            trial_dir, record, operation_name, metrics.get(metric_name)
        )
        referenced_paths.add(log_path)

    evidence = _object(trial.get("evidence"), "Build trial evidence")
    raw = {}
    for name in (
        "source_probe",
        "application_artifacts",
        "image_artifacts",
        "trial_inputs",
    ):
        reference = _object(evidence.get(name), f"Build evidence {name}")
        raw[name] = _validated_reference(trial_dir, reference)
        referenced_paths.add(str(reference["path"]))

    _require_changed(raw["source_probe"], "sha256", "source probe")
    _require_changed(raw["application_artifacts"], "sha256", "application artifact")
    _require_changed(raw["image_artifacts"], "image_digest", "image")
    _validate_source_probe(raw["source_probe"], manifest, root_dir)
    _validate_application_artifacts(raw["application_artifacts"], manifest)
    referenced_paths.update(
        _validate_image_artifacts(raw["image_artifacts"], trial_dir)
    )

    artifact_reference = _object(
        trial.get("artifact_manifest"), "Build artifact manifest reference"
    )
    artifact_manifest = _validated_reference(trial_dir, artifact_reference)
    _validate_artifact_manifest(
        trial_dir,
        artifact_manifest,
        str(trial.get("trial_id")),
        referenced_paths,
    )

    trial_inputs = raw["trial_inputs"]
    seed_digest = trial_inputs.get("dependency_seed_sha256")
    if not _valid_digest(seed_digest) or seed_digest != trial_inputs.get(
        "dependency_cache_initial_sha256"
    ):
        raise ValueError("Fresh dependency cache does not match immutable seed")
    _add_unique(workspaces, trial_inputs.get("workspace"), "workspace")
    _add_unique(caches, trial_inputs.get("dependency_cache"), "dependency cache")
    _add_unique(builders, trial_inputs.get("builder_name"), "builder")
    if not _valid_digest(trial_inputs.get("cache_seed_sha256")):
        raise ValueError("BuildKit cache seed digest is invalid")
    if trial_inputs.get("dependency_seed_sha256") != cache_seed.get(
        "dependency_seed_sha256"
    ):
        raise ValueError("Trial dependency seed does not match campaign seed")
    if trial_inputs.get("cache_seed_sha256") != cache_seed.get(
        "buildkit_cache_seed_sha256"
    ):
        raise ValueError("Trial BuildKit cache seed does not match campaign seed")
    _validate_builder_context(trial, trial_inputs, cache_seed)
    _validate_operation_argv(
        operation_records,
        trial_inputs,
        cache_seed,
        manifest,
        trial,
    )
    return trial_inputs


def _validate_operation_record(
    trial_dir: Path,
    record: dict[str, Any],
    operation_name: str,
    metric_value: object,
) -> str:
    if record.get("name") != operation_name:
        raise ValueError("Raw build operation name is invalid")
    if record.get("exit_code") != 0:
        raise ValueError(f"Build operation failed: {operation_name}")
    if not isinstance(record.get("argv"), list) or not record["argv"]:
        raise ValueError(f"Build operation argv is invalid: {operation_name}")
    start = record.get("start_monotonic_ns")
    end = record.get("end_monotonic_ns")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        raise ValueError(f"Build operation monotonic boundary is invalid: {operation_name}")
    recomputed = (end - start) / 1_000_000
    for value, field in (
        (record.get("duration_ms"), "raw duration"),
        (metric_value, "trial metric"),
    ):
        if not isinstance(value, (int, float)) or not math.isclose(
            float(value), recomputed, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError(f"Build operation {field} mismatch: {operation_name}")
    for field in ("started_at", "finished_at"):
        if not isinstance(record.get(field), str):
            raise ValueError(f"Build operation {field} is missing: {operation_name}")
    log_reference = _object(record.get("combined_log"), "Build operation combined log")
    log_path = _contained_file(trial_dir, str(log_reference.get("path", "")))
    if log_path.stat().st_size != log_reference.get("size_bytes"):
        raise ValueError(f"Build operation log size mismatch: {operation_name}")
    if _sha256_file(log_path) != log_reference.get("sha256"):
        raise ValueError(f"Build operation log digest mismatch: {operation_name}")
    return str(log_reference["path"])


def _validate_builder_context(
    trial: dict[str, Any],
    trial_inputs: dict[str, Any],
    cache_seed: dict[str, Any],
) -> None:
    trial_id = str(trial.get("trial_id", ""))
    try:
        trial_index = int(trial_id.removeprefix("trial-"))
    except ValueError:
        raise ValueError("Build trial builder context is invalid") from None
    campaign = hashlib.sha256(str(trial.get("run_id", "")).encode()).hexdigest()[:16]
    expected_builder = f"hrw-build-{campaign}-{trial_index:02d}"
    expected_seed_builder = f"hrw-build-{campaign}-seed"
    expected = {
        "builder_name": expected_builder,
        "builder_driver": "docker-container",
        "builder_image": _BUILDKIT_IMAGE,
        "builder_cpu_quota": 200000,
        "builder_cpu_period": 100000,
        "builder_memory": "4g",
        "builder_memory_swap": "4g",
        "state_volume": f"buildx_buildkit_{expected_builder}0_state",
    }
    for field, value in expected.items():
        if trial_inputs.get(field) != value:
            raise ValueError(f"Build operation argv builder context is invalid: {field}")
    if cache_seed.get("seed_builder_name") != expected_seed_builder:
        raise ValueError("Build operation argv seed builder context is invalid")
    if cache_seed.get("seed_state_volume") != (
        f"buildx_buildkit_{expected_seed_builder}0_state"
    ):
        raise ValueError("Build operation argv seed state volume is invalid")
    cache_path = cache_seed.get("buildkit_cache_seed_path")
    if not isinstance(cache_path, str) or not cache_path:
        raise ValueError("Build operation argv cache seed path is invalid")
    if trial_inputs.get("buildkit_cache_seed") != cache_path:
        raise ValueError("Build operation argv cache seed path does not match campaign")


def _validate_operation_argv(
    records: list[dict[str, Any]],
    trial_inputs: dict[str, Any],
    cache_seed: dict[str, Any],
    manifest: dict[str, Any],
    trial: dict[str, Any],
) -> None:
    execution = _object(manifest.get("execution"), "Resolved build execution")
    build = _object(execution.get("build"), "Resolved build inputs")
    workspace = Path(str(trial_inputs.get("workspace", "")))
    dependency_cache = str(trial_inputs.get("dependency_cache", ""))
    trial_scratch = workspace.parent
    if (
        not workspace.is_absolute()
        or workspace.name != "workspace"
        or trial_scratch.name != trial.get("trial_id")
    ):
        raise ValueError("Build operation argv workspace context is invalid")
    if dependency_cache != str(trial_scratch / "dependency-cache"):
        raise ValueError("Build operation argv dependency cache context is invalid")
    expected_cache_seed = str(trial_scratch.parent / "buildkit-cache-seed")
    if cache_seed.get("buildkit_cache_seed_path") != expected_cache_seed:
        raise ValueError("Build operation argv campaign cache context is invalid")
    evidence_dir = Path(str(trial_inputs.get("trial_evidence_dir", "")))
    expected_trial_directory = str(trial.get("trial_id", "")).removeprefix("trial-")
    if (
        not evidence_dir.is_absolute()
        or evidence_dir.name != expected_trial_directory
        or evidence_dir.parent.name != "trials"
    ):
        raise ValueError("Build operation argv trial evidence context is invalid")
    app_dir = workspace / str(execution.get("app_dir", ""))
    commands = {
        "gradle_clean_build": build.get("clean_command"),
        "gradle_incremental_rebuild": build.get("incremental_command"),
    }
    for operation_name, command in commands.items():
        if not isinstance(command, list) or any(
            not isinstance(argument, str) for argument in command
        ):
            raise ValueError(f"Build operation argv manifest is invalid: {operation_name}")
        record = next(record for record in records if record["name"] == operation_name)
        argv = record["argv"]
        user = _docker_option(argv, "--user", operation_name)
        if re.fullmatch(r"[0-9]+:[0-9]+", user) is None:
            raise ValueError(f"Build operation argv user is invalid: {operation_name}")
        expected = [
            "docker",
            "run",
            "--rm",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "--memory-swap",
            "4g",
            "--user",
            user,
            "--network",
            "none",
            "--mount",
            f"type=bind,src={app_dir},target=/workspace",
            "--mount",
            f"type=bind,src={dependency_cache},target=/gradle-cache",
            "--env",
            "GRADLE_USER_HOME=/gradle-cache",
            "--workdir",
            "/workspace",
            _GRADLE_EXECUTOR_IMAGE,
            *command,
        ]
        _require_exact_argv(argv, expected, operation_name)

    image_operations = (
        (
            "image_package",
            "image-package",
            "image_package_archive",
            "image_package_metadata",
            True,
        ),
        (
            "image_rebuild",
            "image-rebuild",
            "image_rebuild_archive",
            "image_rebuild_metadata",
            False,
        ),
    )
    for (
        operation_name,
        filename,
        archive_field,
        metadata_field,
        imports_seed,
    ) in image_operations:
        expected = [
            "docker",
            "buildx",
            "build",
            "--builder",
            str(trial_inputs["builder_name"]),
            "--platform",
            "linux/amd64",
            "--provenance=false",
            "--file",
            str(app_dir / str(build.get("dockerfile", ""))),
        ]
        if imports_seed:
            expected.extend(
                [
                    "--cache-from",
                    f"type=local,src={cache_seed['buildkit_cache_seed_path']}",
                ]
            )
        metadata_path = trial_inputs.get(metadata_field)
        if not isinstance(metadata_path, str) or not metadata_path:
            raise ValueError(f"Build operation argv metadata path is invalid: {operation_name}")
        if metadata_path != str(trial_scratch / (filename + "-metadata.json")):
            raise ValueError(f"Build operation argv metadata context is invalid: {operation_name}")
        archive_path = trial_inputs.get(archive_field)
        if not isinstance(archive_path, str) or not archive_path:
            raise ValueError(f"Build operation argv archive path is invalid: {operation_name}")
        if archive_path != str(evidence_dir / (filename + ".oci")):
            raise ValueError(f"Build operation argv archive path is invalid: {operation_name}")
        expected.extend(
            [
                "--output",
                f"type=oci,dest={archive_path}",
                "--metadata-file",
                metadata_path,
                str(app_dir / str(build.get("context", ""))),
            ]
        )
        record = next(record for record in records if record["name"] == operation_name)
        _require_exact_argv(record["argv"], expected, operation_name)


def _docker_option(argv: object, option: str, operation_name: str) -> str:
    if not isinstance(argv, list) or argv.count(option) != 1:
        raise ValueError(f"Build operation argv option is invalid: {operation_name}")
    index = argv.index(option)
    if index + 1 >= len(argv) or not isinstance(argv[index + 1], str):
        raise ValueError(f"Build operation argv option is invalid: {operation_name}")
    return argv[index + 1]


def _require_exact_argv(
    actual: object, expected: list[str], operation_name: str
) -> None:
    if actual != expected:
        raise ValueError(f"Build operation argv does not match contract: {operation_name}")


def _validate_artifact_manifest(
    trial_dir: Path,
    manifest: dict[str, Any],
    trial_id: str,
    required_paths: set[str],
) -> None:
    if manifest.get("schema_version") != "1.0" or manifest.get("trial_id") != trial_id:
        raise ValueError("Build artifact manifest identity is invalid")
    artifacts = _object_list(manifest.get("artifacts"), "Build artifacts")
    paths = [str(artifact.get("path", "")) for artifact in artifacts]
    if len(paths) != len(set(paths)):
        raise ValueError("Build artifact manifest paths must be unique")
    if set(paths) != required_paths:
        raise ValueError("Build artifact manifest does not match referenced raw evidence")
    for artifact, relative_path in zip(artifacts, paths):
        if relative_path.endswith(".oci"):
            raise ValueError("OCI archives must not be retained in build evidence")
        path = _contained_file(trial_dir, relative_path)
        if path.stat().st_size != artifact.get("size_bytes"):
            raise ValueError(f"Build artifact size mismatch: {relative_path}")
        if _sha256_file(path) != artifact.get("sha256"):
            raise ValueError(f"Build artifact digest mismatch: {relative_path}")


def _validate_image_artifacts(
    value: dict[str, Any], trial_dir: Path
) -> set[str]:
    referenced_paths = set()
    for phase in ("before", "after"):
        descriptor = _object(value.get(phase), f"Image artifact {phase}")
        referenced_paths.update(validate_oci_descriptor(descriptor, trial_dir))
    if any(path.suffix == ".oci" for path in trial_dir.rglob("*.oci")):
        raise ValueError("OCI archives must be deleted after metadata extraction")
    return referenced_paths


def validate_oci_descriptor(
    descriptor: dict[str, Any], trial_dir: Path
) -> set[str]:
    image_digest = str(descriptor.get("image_digest", ""))
    if not _OCI_DIGEST.fullmatch(image_digest):
        raise ValueError("Image digest is invalid")
    if not _valid_digest(descriptor.get("archive_sha256")):
        raise ValueError("OCI archive digest is invalid")
    if not isinstance(descriptor.get("archive_size_bytes"), int) or descriptor[
        "archive_size_bytes"
    ] < 1:
        raise ValueError("OCI archive size is invalid")

    index_path = _validated_binary_reference(
        trial_dir, descriptor.get("index"), "OCI index"
    )
    manifest_path = _validated_binary_reference(
        trial_dir, descriptor.get("manifest"), "OCI manifest"
    )
    index_bytes = index_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    try:
        index = json.loads(index_bytes)
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Retained OCI metadata is invalid JSON") from None
    if not isinstance(index, dict) or index.get("schemaVersion") != 2:
        raise ValueError("OCI index is structurally invalid")
    manifests = index.get("manifests")
    if not isinstance(manifests, list) or len(manifests) != 1:
        raise ValueError("OCI index must select exactly one manifest")
    selected = manifests[0]
    if not isinstance(selected, dict):
        raise ValueError("OCI index manifest descriptor is invalid")
    _validate_descriptor(
        selected,
        expected_media_type="application/vnd.oci.image.manifest.v1+json",
        label="OCI index manifest",
    )
    if selected.get("digest") != image_digest:
        raise ValueError("OCI index manifest digest does not match image digest")
    if selected.get("size") != len(manifest_bytes):
        raise ValueError("OCI index manifest size does not match retained bytes")
    actual_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    if actual_digest != image_digest:
        raise ValueError("Retained OCI manifest bytes do not match image digest")
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
        raise ValueError("OCI manifest is structurally invalid")
    _validate_descriptor(
        manifest.get("config"),
        expected_media_type="application/vnd.oci.image.config.v1+json",
        label="OCI config",
    )
    layers = manifest.get("layers")
    if not isinstance(layers, list):
        raise ValueError("OCI manifest layers are invalid")
    for layer in layers:
        _validate_descriptor(layer, label="OCI layer")
    return {
        str(_object(descriptor.get("index"), "OCI index reference")["path"]),
        str(_object(descriptor.get("manifest"), "OCI manifest reference")["path"]),
    }


def _validate_descriptor(
    value: object,
    *,
    label: str,
    expected_media_type: str | None = None,
) -> None:
    descriptor = _object(value, label)
    media_type = descriptor.get("mediaType")
    if not isinstance(media_type, str) or not media_type:
        raise ValueError(f"{label} mediaType is invalid")
    if expected_media_type is not None and media_type != expected_media_type:
        raise ValueError(f"{label} mediaType is invalid")
    if not _OCI_DIGEST.fullmatch(str(descriptor.get("digest", ""))):
        raise ValueError(f"{label} digest is invalid")
    if not isinstance(descriptor.get("size"), int) or descriptor["size"] < 0:
        raise ValueError(f"{label} size is invalid")


def _validated_binary_reference(
    directory: Path, reference: object, label: str
) -> Path:
    value = _object(reference, f"{label} reference")
    path = _contained_file(directory, str(value.get("path", "")))
    if path.stat().st_size != value.get("size_bytes"):
        raise ValueError(f"{label} retained size mismatch")
    if _sha256_file(path) != value.get("sha256"):
        raise ValueError(f"{label} retained digest mismatch")
    return path


def _validate_host_evidence(
    preflight: dict[str, Any], postflight: dict[str, Any]
) -> None:
    expected = {
        "machine_id": "f66cd2d134b94bb18eb7e531d1baf343",
        "cpu_model": "AMD Ryzen 7 5825U",
    }
    for phase, evidence in (("preflight", preflight), ("postflight", postflight)):
        for field, value in expected.items():
            if evidence.get(field) != value:
                raise ValueError(f"Build host {phase} {field} is invalid")
        if not isinstance(evidence.get("logical_cpu_count"), int) or evidence[
            "logical_cpu_count"
        ] < 16:
            raise ValueError(f"Build host {phase} CPU count is invalid")
        if not isinstance(evidence.get("memory_bytes"), int) or evidence[
            "memory_bytes"
        ] < 29_313_151_795:
            raise ValueError(f"Build host {phase} memory is invalid")
        for field in ("docker_version", "buildx_version"):
            if not isinstance(evidence.get(field), str) or not evidence[field]:
                raise ValueError(f"Build host {phase} {field} is missing")
    stable_fields = (
        "machine_id",
        "cpu_model",
        "logical_cpu_count",
        "memory_bytes",
        "docker_version",
        "buildx_version",
    )
    for field in stable_fields:
        if preflight.get(field) != postflight.get(field):
            raise ValueError(f"Build host changed during measurement: {field}")
    preflight_containers = preflight.get("running_containers")
    postflight_containers = postflight.get("running_containers")
    if not isinstance(preflight_containers, list) or not isinstance(
        postflight_containers, list
    ):
        raise ValueError("Build host running container evidence is invalid")
    if set(preflight_containers) != set(postflight_containers):
        raise ValueError("Build host running container set changed during measurement")
    builders = postflight.get("builders", [])
    if not isinstance(builders, list) or any(
        str(builder).startswith("hrw-build-") for builder in builders
    ):
        raise ValueError("Benchmark Buildx builder remains after measurement")
    volumes = postflight.get("buildkit_state_volumes", [])
    if not isinstance(volumes, list) or any("hrw-build-" in str(volume) for volume in volumes):
        raise ValueError("Benchmark BuildKit state volume remains after measurement")


def _validate_cache_seed(
    cache_seed: dict[str, Any],
    run_set_dir: Path,
    root_dir: Path,
    manifest: dict[str, Any],
) -> None:
    if cache_seed.get("gradle_executor_image") != _GRADLE_EXECUTOR_IMAGE:
        raise ValueError("Campaign dependency seed executor is invalid")
    if cache_seed.get("buildkit_image") != _BUILDKIT_IMAGE:
        raise ValueError("Campaign BuildKit seed image is invalid")
    for field in ("dependency_seed_sha256", "buildkit_cache_seed_sha256"):
        if not _valid_digest(cache_seed.get(field)):
            raise ValueError(f"Campaign seed field is invalid: {field}")
    if cache_seed.get("dependency_seed_mode") not in {"supplied", "prepared"}:
        raise ValueError("Campaign dependency seed mode is invalid")
    for field in (
        "workspace_build_outputs_removed",
        "gradle_runtime_state_removed",
    ):
        if cache_seed.get(field) is not True:
            raise ValueError(f"Campaign dependency seed cleanup is invalid: {field}")

    operation_reference = _object(
        cache_seed.get("buildkit_seed_operation"),
        "BuildKit seed operation reference",
    )
    operation = _validated_reference(run_set_dir, operation_reference)
    _validate_operation_record(
        run_set_dir,
        operation,
        "buildkit_runtime_base_seed",
        operation.get("duration_ms"),
    )
    if operation.get("working_directory") != "repository-root":
        raise ValueError("BuildKit seed working directory is invalid")

    execution = _object(manifest.get("execution"), "Resolved build execution")
    build = _object(execution.get("build"), "Resolved build inputs")
    app_dir = PurePosixPath(str(execution.get("app_dir", "")))
    dockerfile = app_dir / str(build.get("dockerfile", ""))
    context = (
        app_dir
        if build.get("context") == "."
        else app_dir / str(build.get("context", ""))
    )
    expected_argv = [
        "docker",
        "buildx",
        "build",
        "--builder",
        str(cache_seed.get("seed_builder_name", "")),
        "--platform",
        "linux/amd64",
        "--provenance=false",
        "--file",
        dockerfile.as_posix(),
        "--target",
        "runtime-base",
        "--cache-to",
        f"type=local,dest={cache_seed.get('buildkit_cache_seed_path')},mode=max",
        "--output",
        "type=cacheonly",
        context.as_posix(),
    ]
    if operation.get("argv") != expected_argv:
        raise ValueError("BuildKit seed argv does not match the resolved contract")

    seed_tree = root_dir / Path(context.as_posix())
    try:
        seed_tree.resolve(strict=True).relative_to(root_dir.resolve(strict=True))
    except (FileNotFoundError, ValueError):
        raise ValueError("BuildKit seed tree path is invalid") from None
    if cache_seed.get("buildkit_seed_tree_sha256") != _hash_directory(seed_tree):
        raise ValueError("BuildKit seed tree digest does not match the trusted checkout")


def _validate_closed_run_set_files(
    run_set_dir: Path,
    run_set: dict[str, Any],
    cache_seed: dict[str, Any],
) -> list[str]:
    if run_set_dir.is_symlink() or not run_set_dir.is_dir():
        raise ValueError("Build raw run set directory is invalid")
    allowed = {
        "build-run-set.json",
        "build-resolved-manifest.json",
    }
    campaign_evidence = _object(
        run_set.get("campaign_evidence"),
        "Campaign evidence",
    )
    for name in ("preflight", "postflight", "cache_seed"):
        reference = _object(
            campaign_evidence.get(name),
            f"Campaign evidence {name}",
        )
        allowed.add(str(reference["path"]))

    seed_operation_reference = _object(
        cache_seed.get("buildkit_seed_operation"),
        "BuildKit seed operation reference",
    )
    allowed.add(str(seed_operation_reference["path"]))
    seed_operation = _read_object(run_set_dir / str(seed_operation_reference["path"]))
    seed_log = _object(
        seed_operation.get("combined_log"),
        "BuildKit seed operation combined log",
    )
    allowed.add(str(seed_log["path"]))

    for trial_reference in _object_list(run_set.get("trials"), "Build trials"):
        trial_path = str(trial_reference["path"])
        allowed.add(trial_path)
        trial = _read_object(run_set_dir / trial_path)
        trial_dir = PurePosixPath(trial_path).parent
        artifact_reference = _object(
            trial.get("artifact_manifest"),
            "Build artifact manifest reference",
        )
        artifact_path = (trial_dir / str(artifact_reference["path"])).as_posix()
        allowed.add(artifact_path)
        artifact_manifest = _read_object(run_set_dir / artifact_path)
        for artifact in _object_list(
            artifact_manifest.get("artifacts"),
            "Build artifacts",
        ):
            allowed.add((trial_dir / str(artifact["path"])).as_posix())

    actual: set[str] = set()
    for path in run_set_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError("Build raw run set contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(run_set_dir).as_posix())
    if actual != allowed:
        raise ValueError("Build raw run set does not match the closed file set")
    return sorted(allowed)


def _validate_source_probe(
    source_probe: dict[str, Any],
    manifest: dict[str, Any],
    root_dir: Path,
) -> None:
    execution = _object(manifest.get("execution"), "Resolved build execution")
    build = _object(execution.get("build"), "Resolved build inputs")
    incremental_input = _object(
        build.get("incremental_input"),
        "Resolved incremental input",
    )
    expected_path = str(incremental_input.get("path", ""))
    if source_probe.get("path") != expected_path:
        raise ValueError("Build source probe path does not match the resolved contract")
    probe_path = root_dir / str(execution.get("app_dir", "")) / expected_path
    try:
        contents = probe_path.read_bytes()
    except OSError:
        raise ValueError("Build source probe is missing from the trusted checkout") from None
    from_bytes = str(incremental_input.get("from", "")).encode()
    to_bytes = str(incremental_input.get("to", "")).encode()
    if contents.count(from_bytes) != 1:
        raise ValueError("Build source probe from text is invalid")
    mutated = contents.replace(from_bytes, to_bytes, 1)
    expected = {
        "before": hashlib.sha256(contents).hexdigest(),
        "after": hashlib.sha256(mutated).hexdigest(),
    }
    for phase, digest in expected.items():
        descriptor = _object(source_probe.get(phase), f"Source probe {phase}")
        if descriptor.get("sha256") != digest:
            raise ValueError(f"Build source probe {phase} digest is invalid")


def _validate_application_artifacts(
    artifacts: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    execution = _object(manifest.get("execution"), "Resolved build execution")
    build = _object(execution.get("build"), "Resolved build inputs")
    declaration = _object(
        build.get("application_artifact"),
        "Resolved application artifact",
    )
    expected_type = declaration.get("type")
    expected_path = declaration.get("path")
    for phase in ("before", "after"):
        descriptor = _object(
            artifacts.get(phase),
            f"Application artifact {phase}",
        )
        if (
            descriptor.get("type") != expected_type
            or descriptor.get("declaration") != expected_path
        ):
            raise ValueError("Build application artifact declaration is invalid")
        files = _object_list(
            descriptor.get("files"),
            f"Application artifact {phase} files",
        )
        paths = [str(entry.get("path", "")) for entry in files]
        if not paths or paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("Build application artifact file set is invalid")
        for entry, path in zip(files, paths):
            relative = PurePosixPath(path)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise ValueError("Build application artifact path is invalid")
            if expected_type == "glob" and not relative.match(str(expected_path)):
                raise ValueError("Build application artifact path is outside its declaration")
            if expected_type == "directory":
                directory = PurePosixPath(str(expected_path))
                if relative == directory or directory not in relative.parents:
                    raise ValueError("Build application artifact path is outside its declaration")
            if (
                not isinstance(entry.get("size_bytes"), int)
                or entry["size_bytes"] < 0
                or not _valid_digest(entry.get("sha256"))
            ):
                raise ValueError("Build application artifact file metadata is invalid")
        aggregate = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if descriptor.get("sha256") != aggregate:
            raise ValueError("Build application artifact aggregate digest is invalid")


def _require_changed(value: dict[str, Any], field: str, label: str) -> None:
    before = _object(value.get("before"), f"{label} before").get(field)
    after = _object(value.get("after"), f"{label} after").get(field)
    if before == after:
        raise ValueError(f"The {label} digest did not change")


def _validated_reference(directory: Path, reference: object) -> dict[str, Any]:
    value = _object(reference, "Evidence reference")
    path = _contained_file(directory, str(value.get("path", "")))
    if _sha256_file(path) != value.get("sha256"):
        raise ValueError(f"Evidence digest mismatch: {value.get('path')}")
    return _read_object(path)


def _contained_file(directory: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"Invalid build evidence path: {relative_path}")
    base = directory.resolve(strict=True)
    path = base / relative
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(base)
    except (FileNotFoundError, ValueError):
        raise ValueError(f"Invalid build evidence path: {relative_path}") from None
    cursor = base
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f"Invalid build evidence path: {relative_path}")
    if resolved != path or not path.is_file():
        raise ValueError(f"Invalid build evidence path: {relative_path}")
    return path


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _object_list(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be an object list")
    return value


def _add_unique(values: set[str], value: object, label: str) -> None:
    if not isinstance(value, str) or not value or value in values:
        raise ValueError(f"Build trial {label} must be unique")
    values.add(value)


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_directory(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("BuildKit seed tree contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
