from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .build_config import BuildRunConfig
from .build_evidence import (
    summarize_build_trials,
    validate_build_document,
    validate_build_publication_evidence,
)
from .build_manifest import (
    build_resolved_build_manifest,
    validate_resolved_build_manifest,
)
from .evidence import build_artifact_manifest, sha256_file
from .manifest import read_git_provenance
from .results import write_json


GRADLE_EXECUTOR_IMAGE = (
    "eclipse-temurin:25-jdk@sha256:"
    "68868d04fa9cfd5f5c6abec0b5cef86d8de2bf9c62c37c7d3e4f0f80f5cfd7ff"
)
BUILDKIT_IMAGE = (
    "moby/buildkit:buildx-stable-1@sha256:"
    "0168606be2315b7c807a03b3d8aa79beefdb31c98740cebdffdfeebf31190c9f"
)
_PROBE_IMPLEMENTATIONS = ("spring-boot", "quarkus")
_OPERATION_METRICS = (
    ("gradle_clean_build", "gradle_clean_build_ms"),
    ("image_package", "image_package_ms"),
    ("gradle_incremental_rebuild", "gradle_incremental_rebuild_ms"),
    ("image_rebuild", "image_rebuild_ms"),
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def run_build_benchmark_set(
    config: BuildRunConfig,
    *,
    command_runner: CommandRunner | None = None,
    dependency_seed: Path | None = None,
    results_root: Path | None = None,
    host_probe: Callable[[], dict[str, Any]] | None = None,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    utc_now: Callable[[], str] | None = None,
) -> Path:
    command_runner = command_runner or _run_command
    utc_now = utc_now or _utc_timestamp
    _validate_config(config)
    validate_probe_sources(config.root_dir, config.build["incremental_input"])

    run_id = _build_run_id(config)
    base = results_root or (
        config.root_dir
        / "results"
        / config.language
        / config.framework
        / config.variant
        / "build"
    )
    result_dir = base / run_id
    result_dir.mkdir(parents=True, exist_ok=False)
    scratch = Path(tempfile.mkdtemp(prefix=f"{run_id}-", dir=base.parent))
    host_reader = host_probe or (lambda: _collect_host_evidence(command_runner))

    try:
        source = read_git_provenance(config.root_dir)
        manifest = build_resolved_build_manifest(config, run_id, source)
        validate_resolved_build_manifest(manifest, config.root_dir)
        write_json(result_dir / "build-resolved-manifest.json", manifest)

        preflight = _normalized_host_evidence(host_reader())
        _validate_preflight(preflight, config)
        write_json(result_dir / "preflight.json", preflight)

        _execute(command_runner, ["docker", "pull", GRADLE_EXECUTOR_IMAGE])
        _execute(command_runner, ["docker", "pull", BUILDKIT_IMAGE])

        seed, seed_mode, seed_cleanup = _resolve_dependency_seed(
            config,
            dependency_seed,
            scratch,
            command_runner,
        )
        dependency_seed_digest = _hash_directory(seed)
        cache_seed_dir = scratch / "buildkit-cache-seed"
        _prepare_buildkit_cache_seed(
            config,
            run_id,
            cache_seed_dir,
            scratch,
            command_runner,
        )
        buildkit_cache_seed_digest = _hash_directory(cache_seed_dir)
        builder_resources = _builder_resources(run_id, 1)
        cache_seed_document = {
            "gradle_executor_image": GRADLE_EXECUTOR_IMAGE,
            "buildkit_image": BUILDKIT_IMAGE,
            "dependency_seed_sha256": dependency_seed_digest,
            "buildkit_cache_seed_sha256": buildkit_cache_seed_digest,
            "buildkit_cache_seed_path": str(cache_seed_dir),
            "seed_builder_name": builder_resources["seed_builder"],
            "seed_state_volume": builder_resources["seed_state_volume"],
            "dependency_seed_mode": seed_mode,
            "workspace_build_outputs_removed": seed_cleanup[
                "workspace_build_outputs_removed"
            ],
            "gradle_runtime_state_removed": seed_cleanup[
                "gradle_runtime_state_removed"
            ],
        }
        write_json(result_dir / "cache-seed.json", cache_seed_document)

        trials = []
        trial_references = []
        for index in range(1, 4):
            trial = _run_trial(
                config,
                result_dir,
                scratch,
                run_id,
                index,
                manifest,
                seed,
                dependency_seed_digest,
                cache_seed_dir,
                buildkit_cache_seed_digest,
                command_runner,
                monotonic_ns,
                utc_now,
            )
            trials.append(trial)
            trial_path = result_dir / "trials" / f"{index:02d}" / "build-trial.json"
            trial_references.append(
                {
                    "trial_id": trial["trial_id"],
                    "index": index,
                    "status": trial["status"],
                    "path": f"trials/{index:02d}/build-trial.json",
                    "sha256": sha256_file(trial_path),
                }
            )

        postflight = _normalized_host_evidence(host_reader())
        write_json(result_dir / "postflight.json", postflight)
        run_set = {
            "schema_version": "1.0",
            "run_set_id": run_id,
            "run_id": run_id,
            "manifest_digest": manifest["manifest_digest"],
            "cohort_fingerprint": manifest["cohort"]["fingerprint"],
            "status": "complete",
            "expected_trials": 3,
            "trials": trial_references,
            "campaign_evidence": {
                "preflight": _reference(result_dir, result_dir / "preflight.json"),
                "postflight": _reference(result_dir, result_dir / "postflight.json"),
                "cache_seed": _reference(result_dir, result_dir / "cache-seed.json"),
            },
            "summary": summarize_build_trials(trials),
        }
        validate_build_document(run_set, "build-run-set", config.root_dir)
        write_json(result_dir / "build-run-set.json", run_set)
        validate_build_publication_evidence(result_dir, config.root_dir)
        return result_dir
    except Exception as error:
        write_json(
            result_dir / "failure.json",
            {
                "failure_type": type(error).__name__,
                "message": str(error),
            },
        )
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def validate_probe_sources(
    root_dir: Path, incremental_input: Mapping[str, object]
) -> None:
    relative_path = str(incremental_input["path"])
    from_text = str(incremental_input["from"])
    contents = []
    for implementation in _PROBE_IMPLEMENTATIONS:
        path = root_dir / "implementations/java" / implementation / relative_path
        if not path.is_file():
            raise ValueError(f"Build benchmark probe is missing: {path}")
        content = path.read_bytes()
        if content.count(from_text.encode()) != 1:
            raise ValueError(
                f"Build benchmark probe must contain exact from text once: {path}"
            )
        contents.append(content)
    if contents[0] != contents[1]:
        raise ValueError("Build benchmark probes must be byte-identical")


def mutate_probe(path: Path, from_text: str, to_text: str) -> tuple[str, str]:
    content = path.read_text()
    if content.count(from_text) != 1:
        raise ValueError("Build benchmark probe must contain from text exactly once")
    before = hashlib.sha256(content.encode()).hexdigest()
    updated = content.replace(from_text, to_text, 1)
    path.write_text(updated)
    return before, hashlib.sha256(updated.encode()).hexdigest()


def _run_trial(
    config: BuildRunConfig,
    result_dir: Path,
    scratch: Path,
    run_id: str,
    index: int,
    manifest: dict[str, Any],
    dependency_seed: Path,
    dependency_seed_digest: str,
    cache_seed_dir: Path,
    cache_seed_digest: str,
    command_runner: CommandRunner,
    monotonic_ns: Callable[[], int],
    utc_now: Callable[[], str],
) -> dict[str, Any]:
    trial_id = f"trial-{index:02d}"
    trial_dir = result_dir / "trials" / f"{index:02d}"
    trial_dir.mkdir(parents=True)
    trial_scratch = scratch / trial_id
    workspace = trial_scratch / "workspace"
    dependency_cache = trial_scratch / "dependency-cache"
    _prepare_git_workspace(config, workspace, trial_scratch, command_runner)
    shutil.copytree(dependency_seed, dependency_cache)
    initial_cache_digest = _hash_directory(dependency_cache)
    if initial_cache_digest != dependency_seed_digest:
        raise ValueError("Fresh dependency cache copy does not match seed")
    app_dir = workspace / config.app_dir.relative_to(config.root_dir)
    probe_input = config.build["incremental_input"]
    assert isinstance(probe_input, dict)
    probe_path = app_dir / str(probe_input["path"])
    if probe_path.read_text().count(str(probe_input["from"])) != 1:
        raise ValueError("Archived source probe does not contain exact from text")

    resources = _builder_resources(run_id, index)
    builder_name = resources["trial_builder"]
    state_volume = resources["trial_state_volume"]
    _create_builder(builder_name, command_runner)
    operations = []
    metrics = {}
    started_at = utc_now()
    builder_removed = False
    state_volume_removed = False
    try:
        clean_argv = _gradle_argv(
            app_dir, dependency_cache, list(config.build["clean_command"])
        )
        clean_record = _measure_operation(
            "gradle_clean_build",
            clean_argv,
            trial_dir,
            1,
            command_runner,
            monotonic_ns,
            utc_now,
        )
        operations.append(clean_record["reference"])
        metrics["gradle_clean_build_ms"] = clean_record["duration_ms"]

        application_before = _application_artifact(config, app_dir)
        source_before = sha256_file(probe_path)
        package_archive = trial_dir / "image-package.oci"
        package_metadata = trial_scratch / "image-package-metadata.json"
        package_argv = _image_argv(
            config,
            app_dir,
            builder_name,
            package_archive,
            package_metadata,
            cache_seed_dir,
        )
        package_record = _measure_operation(
            "image_package",
            package_argv,
            trial_dir,
            2,
            command_runner,
            monotonic_ns,
            utc_now,
        )
        operations.append(package_record["reference"])
        metrics["image_package_ms"] = package_record["duration_ms"]
        image_before = _retain_oci_metadata(
            package_archive,
            package_metadata,
            trial_dir,
            "image-package",
        )

        mutation_before, source_after = mutate_probe(
            probe_path,
            str(probe_input["from"]),
            str(probe_input["to"]),
        )
        if mutation_before != source_before:
            raise ValueError("Source probe changed before deterministic mutation")
        incremental_argv = _gradle_argv(
            app_dir, dependency_cache, list(config.build["incremental_command"])
        )
        incremental_record = _measure_operation(
            "gradle_incremental_rebuild",
            incremental_argv,
            trial_dir,
            3,
            command_runner,
            monotonic_ns,
            utc_now,
        )
        operations.append(incremental_record["reference"])
        metrics["gradle_incremental_rebuild_ms"] = incremental_record["duration_ms"]
        application_after = _application_artifact(config, app_dir)

        rebuild_archive = trial_dir / "image-rebuild.oci"
        rebuild_metadata = trial_scratch / "image-rebuild-metadata.json"
        rebuild_argv = _image_argv(
            config,
            app_dir,
            builder_name,
            rebuild_archive,
            rebuild_metadata,
            None,
        )
        rebuild_record = _measure_operation(
            "image_rebuild",
            rebuild_argv,
            trial_dir,
            4,
            command_runner,
            monotonic_ns,
            utc_now,
        )
        operations.append(rebuild_record["reference"])
        metrics["image_rebuild_ms"] = rebuild_record["duration_ms"]
        image_after = _retain_oci_metadata(
            rebuild_archive,
            rebuild_metadata,
            trial_dir,
            "image-rebuild",
        )
    finally:
        builder_removed, state_volume_removed = _remove_builder(
            builder_name, state_volume, command_runner
        )

    evidence_documents = {
        "source_probe": (
            "source-probe.json",
            {
                "path": str(probe_input["path"]),
                "before": {"sha256": source_before},
                "after": {"sha256": source_after},
            },
        ),
        "application_artifacts": (
            "application-artifacts.json",
            {"before": application_before, "after": application_after},
        ),
        "image_artifacts": (
            "image-artifacts.json",
            {"before": image_before, "after": image_after},
        ),
        "trial_inputs": (
            "trial-inputs.json",
            {
                "trial_evidence_dir": str(trial_dir),
                "workspace": str(workspace),
                "dependency_cache": str(dependency_cache),
                "dependency_seed_sha256": dependency_seed_digest,
                "dependency_cache_initial_sha256": initial_cache_digest,
                "buildkit_cache_seed": str(cache_seed_dir),
                "cache_seed_sha256": cache_seed_digest,
                "builder_name": builder_name,
                "builder_driver": "docker-container",
                "builder_image": BUILDKIT_IMAGE,
                "builder_cpu_quota": 200000,
                "builder_cpu_period": 100000,
                "builder_memory": "4g",
                "builder_memory_swap": "4g",
                "state_volume": state_volume,
                "image_package_archive": str(package_archive),
                "image_rebuild_archive": str(rebuild_archive),
                "image_package_metadata": str(package_metadata),
                "image_rebuild_metadata": str(rebuild_metadata),
                "builder_removed": builder_removed,
                "state_volume_removed": state_volume_removed,
            },
        ),
    }
    evidence = {}
    for name, (filename, document) in evidence_documents.items():
        path = trial_dir / filename
        write_json(path, document)
        evidence[name] = _reference(trial_dir, path)

    artifact_manifest = build_artifact_manifest(trial_id, trial_dir)
    artifact_path = trial_dir / "artifact-manifest.json"
    write_json(artifact_path, artifact_manifest)
    trial = {
        "schema_version": "1.0",
        "run_id": run_id,
        "trial_id": trial_id,
        "manifest_digest": manifest["manifest_digest"],
        "cohort_fingerprint": manifest["cohort"]["fingerprint"],
        "status": "valid",
        "started_at": started_at,
        "finished_at": utc_now(),
        "metrics": metrics,
        "operations": operations,
        "evidence": evidence,
        "artifact_manifest": _reference(trial_dir, artifact_path),
    }
    validate_build_document(trial, "build-trial", config.root_dir)
    write_json(trial_dir / "build-trial.json", trial)
    return trial


def _measure_operation(
    name: str,
    argv: list[str],
    trial_dir: Path,
    index: int,
    command_runner: CommandRunner,
    monotonic_ns: Callable[[], int],
    utc_now: Callable[[], str],
) -> dict[str, Any]:
    started_at = utc_now()
    start = monotonic_ns()
    completed = command_runner(argv, cwd=None, check=False)
    end = monotonic_ns()
    finished_at = utc_now()
    output = (completed.stdout or "") + (completed.stderr or "")
    stem = f"{index:02d}-{name}"
    log_path = trial_dir / "operations" / f"{stem}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output)
    duration_ms = (end - start) / 1_000_000
    record = {
        "name": name,
        "argv": argv,
        "started_at": started_at,
        "finished_at": finished_at,
        "start_monotonic_ns": start,
        "end_monotonic_ns": end,
        "duration_ms": duration_ms,
        "exit_code": completed.returncode,
        "combined_log": _sized_reference(trial_dir, log_path),
    }
    record_path = trial_dir / "operations" / f"{stem}.json"
    write_json(record_path, record)
    if completed.returncode != 0:
        raise RuntimeError(f"Build operation failed: {name}")
    return {
        "duration_ms": duration_ms,
        "reference": {"name": name, **_reference(trial_dir, record_path)},
    }


def _gradle_argv(
    app_dir: Path, dependency_cache: Path, gradle_command: list[str]
) -> list[str]:
    return [
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
        f"{os.getuid()}:{os.getgid()}",
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
        GRADLE_EXECUTOR_IMAGE,
        *gradle_command,
    ]


def _image_argv(
    config: BuildRunConfig,
    app_dir: Path,
    builder_name: str,
    archive: Path,
    metadata: Path,
    cache_seed_dir: Path | None,
) -> list[str]:
    argv = [
        "docker",
        "buildx",
        "build",
        "--builder",
        builder_name,
        "--platform",
        "linux/amd64",
        "--provenance=false",
        "--file",
        str(app_dir / str(config.build["dockerfile"])),
    ]
    if cache_seed_dir is not None:
        argv.extend(["--cache-from", f"type=local,src={cache_seed_dir}"])
    argv.extend(
        [
            "--output",
            f"type=oci,dest={archive}",
            "--metadata-file",
            str(metadata),
            str(app_dir / str(config.build["context"])),
        ]
    )
    return argv


def _prepare_git_workspace(
    config: BuildRunConfig,
    workspace: Path,
    scratch: Path,
    command_runner: CommandRunner,
) -> None:
    workspace.mkdir(parents=True)
    scratch.mkdir(parents=True, exist_ok=True)
    archive = scratch / "source.tar"
    relative_app = config.app_dir.relative_to(config.root_dir).as_posix()
    _execute(
        command_runner,
        [
            "git",
            "archive",
            "--format=tar",
            "--output",
            str(archive),
            "HEAD",
            "--",
            relative_app,
        ],
        cwd=config.root_dir,
    )
    with tarfile.open(archive) as source:
        source.extractall(workspace, filter="data")
    archive.unlink()


def _resolve_dependency_seed(
    config: BuildRunConfig,
    supplied_seed: Path | None,
    scratch: Path,
    command_runner: CommandRunner,
) -> tuple[Path, str, dict[str, bool]]:
    if supplied_seed is not None:
        seed = supplied_seed.resolve(strict=True)
        if not seed.is_dir():
            raise ValueError("Dependency seed must be a directory")
        cleanup = {
            "workspace_build_outputs_removed": True,
            "gradle_runtime_state_removed": _gradle_seed_state_absent(seed),
        }
        if not all(cleanup.values()):
            raise ValueError("Supplied dependency seed contains build output or cache state")
        return seed, "supplied", cleanup

    seed_workspace = scratch / "dependency-seed-workspace"
    _prepare_git_workspace(config, seed_workspace, scratch / "seed-source", command_runner)
    app_dir = seed_workspace / config.app_dir.relative_to(config.root_dir)
    seed = scratch / "dependency-seed"
    seed.mkdir()
    online_command = [
        argument
        for argument in list(config.build["clean_command"])
        if argument != "--offline"
    ]
    completed = command_runner(
        [
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
            f"{os.getuid()}:{os.getgid()}",
            "--mount",
            f"type=bind,src={app_dir},target=/workspace",
            "--mount",
            f"type=bind,src={seed},target=/gradle-cache",
            "--env",
            "GRADLE_USER_HOME=/gradle-cache",
            "--workdir",
            "/workspace",
            GRADLE_EXECUTOR_IMAGE,
            *online_command,
        ],
        cwd=None,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Dependency seed preparation failed")
    shutil.rmtree(app_dir / "build", ignore_errors=True)
    workspace_build_outputs_removed = not (app_dir / "build").exists()
    clean_gradle_seed_state(seed)
    cleanup = {
        "workspace_build_outputs_removed": workspace_build_outputs_removed,
        "gradle_runtime_state_removed": _gradle_seed_state_absent(seed),
    }
    if not all(cleanup.values()):
        raise ValueError("Dependency seed cleanup failed")
    return seed, "prepared", cleanup


def clean_gradle_seed_state(seed: Path) -> None:
    for path in seed.glob("caches/build-cache-*"):
        if path.is_dir():
            shutil.rmtree(path)
    for name in ("daemon", "workers"):
        path = seed / name
        if path.is_dir():
            shutil.rmtree(path)


def _gradle_seed_state_absent(seed: Path) -> bool:
    return not any(path.is_dir() for path in seed.glob("caches/build-cache-*")) and not any(
        (seed / name).is_dir() for name in ("daemon", "workers")
    )


def _prepare_buildkit_cache_seed(
    config: BuildRunConfig,
    run_id: str,
    cache_seed_dir: Path,
    scratch: Path,
    command_runner: CommandRunner,
) -> None:
    resources = _builder_resources(run_id, 1)
    builder_name = resources["seed_builder"]
    state_volume = resources["seed_state_volume"]
    _create_builder(builder_name, command_runner)
    try:
        argv = [
            "docker",
            "buildx",
            "build",
            "--builder",
            builder_name,
            "--platform",
            "linux/amd64",
            "--provenance=false",
            "--file",
            str(config.app_dir / str(config.build["dockerfile"])),
            "--target",
            "runtime-base",
            "--cache-to",
            f"type=local,dest={cache_seed_dir},mode=max",
            "--output",
            "type=cacheonly",
            str(config.app_dir / str(config.build["context"])),
        ]
        _execute(command_runner, argv)
    finally:
        removed, volume_removed = _remove_builder(
            builder_name, state_volume, command_runner
        )
        if not removed or not volume_removed:
            raise RuntimeError("BuildKit cache seed builder cleanup failed")
    if not cache_seed_dir.is_dir():
        raise RuntimeError("BuildKit runtime-base cache seed was not exported")


def _builder_resources(run_id: str, trial_index: int) -> dict[str, str]:
    campaign = hashlib.sha256(run_id.encode()).hexdigest()[:16]
    trial_builder = f"hrw-build-{campaign}-{trial_index:02d}"
    seed_builder = f"hrw-build-{campaign}-seed"
    return {
        "trial_builder": trial_builder,
        "trial_state_volume": f"buildx_buildkit_{trial_builder}0_state",
        "seed_builder": seed_builder,
        "seed_state_volume": f"buildx_buildkit_{seed_builder}0_state",
    }


def _create_builder(builder_name: str, command_runner: CommandRunner) -> None:
    _execute(
        command_runner,
        [
            "docker",
            "buildx",
            "create",
            "--name",
            builder_name,
            "--driver",
            "docker-container",
            "--driver-opt",
            f"image={BUILDKIT_IMAGE},cpu-quota=200000,cpu-period=100000,memory=4g,memory-swap=4g",
            "--use",
        ],
    )
    _execute(
        command_runner,
        ["docker", "buildx", "inspect", "--builder", builder_name, "--bootstrap"],
    )


def _remove_builder(
    builder_name: str, state_volume: str, command_runner: CommandRunner
) -> tuple[bool, bool]:
    _execute(
        command_runner,
        ["docker", "buildx", "rm", "--force", builder_name],
        check=False,
    )
    _execute(
        command_runner,
        ["docker", "volume", "rm", "--force", state_volume],
        check=False,
    )
    builders = _execute(
        command_runner,
        ["docker", "buildx", "ls", "--format", "{{.Name}}"],
    ).stdout.splitlines()
    volumes = _execute(
        command_runner,
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
    ).stdout.splitlines()
    return builder_name not in builders, state_volume not in volumes


def _retain_oci_metadata(
    archive_path: Path,
    metadata_path: Path,
    trial_dir: Path,
    operation: str,
) -> dict[str, Any]:
    archive_size = archive_path.stat().st_size
    archive_digest = sha256_file(archive_path)
    metadata = json.loads(metadata_path.read_text())
    image_digest = metadata.get("containerimage.digest")
    if not isinstance(image_digest, str) or not image_digest.startswith("sha256:"):
        raise ValueError("Buildx metadata has no image digest")
    digest = image_digest.removeprefix("sha256:")
    output_dir = trial_dir / "oci" / operation
    output_dir.mkdir(parents=True)
    index_path = output_dir / "index.json"
    manifest_path = output_dir / "manifest.json"
    try:
        with tarfile.open(archive_path) as archive:
            index_bytes = _read_tar_member(archive, "index.json")
            index_path.write_bytes(index_bytes)
            index = json.loads(index_bytes)
            manifests = index.get("manifests", [])
            selected = next(
                (
                    item
                    for item in manifests
                    if isinstance(item, dict) and item.get("digest") == image_digest
                ),
                None,
            )
            if selected is None:
                raise ValueError("OCI index does not select the recorded image digest")
            manifest_path.write_bytes(
                _read_tar_member(archive, f"blobs/sha256/{digest}")
            )
    finally:
        archive_path.unlink(missing_ok=True)
    return {
        "image_digest": image_digest,
        "archive_sha256": archive_digest,
        "archive_size_bytes": archive_size,
        "index": _sized_reference(trial_dir, index_path),
        "manifest": _sized_reference(trial_dir, manifest_path),
    }


def _read_tar_member(archive: tarfile.TarFile, name: str) -> bytes:
    try:
        member = archive.getmember(name)
        file = archive.extractfile(member)
    except KeyError:
        raise ValueError(f"OCI archive is missing {name}") from None
    if file is None or not member.isfile():
        raise ValueError(f"OCI archive member is not a file: {name}")
    return file.read()


def _application_artifact(
    config: BuildRunConfig, app_dir: Path
) -> dict[str, Any]:
    declaration = config.build["application_artifact"]
    assert isinstance(declaration, dict)
    artifact_type = declaration["type"]
    artifact_path = str(declaration["path"])
    if artifact_type == "glob":
        files = sorted(path for path in app_dir.glob(artifact_path) if path.is_file())
    elif artifact_type == "directory":
        directory = app_dir / artifact_path
        if not directory.is_dir():
            raise ValueError(f"Application artifact directory is missing: {artifact_path}")
        files = sorted(path for path in directory.rglob("*") if path.is_file())
    else:
        raise ValueError(f"Unsupported application artifact type: {artifact_type}")
    if not files:
        raise ValueError("Gradle produced no declared application artifact")
    entries = [
        {
            "path": path.relative_to(app_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in files
    ]
    digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "type": artifact_type,
        "declaration": artifact_path,
        "sha256": digest,
        "files": entries,
    }


def _hash_directory(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _collect_host_evidence(command_runner: CommandRunner) -> dict[str, Any]:
    machine_id = Path("/etc/machine-id").read_text().strip()
    cpu_model = platform.processor()
    try:
        lscpu = _execute(command_runner, ["lscpu"]).stdout
        for line in lscpu.splitlines():
            if line.startswith("Model name:"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    memory_bytes = 0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            memory_bytes = int(line.split()[1]) * 1024
            break
    return {
        "machine_id": machine_id,
        "cpu_model": cpu_model,
        "logical_cpu_count": os.cpu_count() or 0,
        "memory_bytes": memory_bytes,
        "docker_version": _execute(
            command_runner,
            ["docker", "version", "--format", "{{.Server.Version}}"],
        ).stdout.strip(),
        "buildx_version": _execute(
            command_runner, ["docker", "buildx", "version"]
        ).stdout.strip(),
        "running_containers": _execute(
            command_runner,
            ["docker", "ps", "--format", "{{.ID}} {{.Image}} {{.Names}}"],
        ).stdout.splitlines(),
        "builders": _execute(
            command_runner,
            ["docker", "buildx", "ls", "--format", "{{.Name}}"],
        ).stdout.splitlines(),
        "buildkit_state_volumes": [
            volume
            for volume in _execute(
                command_runner,
                ["docker", "volume", "ls", "--format", "{{.Name}}"],
            ).stdout.splitlines()
            if volume.startswith("buildx_buildkit_")
        ],
    }


def _normalized_host_evidence(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **value,
        "running_containers": list(value.get("running_containers", [])),
        "builders": list(value.get("builders", [])),
        "buildkit_state_volumes": list(value.get("buildkit_state_volumes", [])),
    }


def _validate_preflight(
    evidence: dict[str, Any], config: BuildRunConfig
) -> None:
    build = config.environment_profile_config["build"]
    assert isinstance(build, dict)
    if evidence.get("machine_id") != build["machine_id"]:
        raise ValueError("Build runner machine ID does not match home-build-v1")
    if evidence.get("cpu_model") != build["cpu_model"]:
        raise ValueError("Build runner CPU does not match home-build-v1")
    if int(evidence.get("logical_cpu_count", 0)) < int(build["min_logical_cpus"]):
        raise ValueError("Build runner CPU count is below home-build-v1")
    if int(evidence.get("memory_bytes", 0)) < int(build["min_memory_bytes"]):
        raise ValueError("Build runner memory is below home-build-v1")
    if any(
        str(builder).startswith("hrw-build-")
        for builder in evidence.get("builders", [])
    ):
        raise ValueError("Benchmark Buildx builder exists before measurement")
    if any(
        "hrw-build-" in str(volume)
        for volume in evidence.get("buildkit_state_volumes", [])
    ):
        raise ValueError("Benchmark BuildKit state volume exists before measurement")


def _validate_config(config: BuildRunConfig) -> None:
    expected = (
        (config.environment_profile_config.get("id"), "home-build-v1"),
        (config.measurement_protocol_config.get("id"), "official-build-v1"),
        (config.build_profile_config.get("id"), "official-gradle-docker-v1"),
    )
    if any(actual != required for actual, required in expected):
        raise ValueError("Only the frozen official build contracts are executable")
    if config.measurement_protocol_config.get("trials") != 3:
        raise ValueError("Official build evidence requires exactly three trials")


def _execute(
    command_runner: CommandRunner,
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = command_runner(argv, cwd=cwd, check=check)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            argv,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _run_command(
    argv: list[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _reference(directory: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(directory).as_posix(),
        "sha256": sha256_file(path),
    }


def _sized_reference(directory: Path, path: Path) -> dict[str, Any]:
    return {**_reference(directory, path), "size_bytes": path.stat().st_size}


def _build_run_id(config: BuildRunConfig) -> str:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")
    return f"{timestamp}_{config.language}_{config.framework}_{config.variant}_build"


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
