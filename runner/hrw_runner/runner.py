from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .commands import run
from .config import RunConfig
from .evidence import (
    build_artifact_manifest,
    build_compact_time_series,
    build_trial_summary,
    sha256_file,
    summarize_trials,
    validate_evidence_document,
    validate_run_set_evidence,
)
from .manifest import (
    build_resolved_manifest,
    read_git_provenance,
    validate_resolved_manifest,
)
from .results import (
    docker_resource_metrics,
    environment_metadata,
    k6_runtime_metrics,
    read_json,
    summarize_startup_samples,
    write_json,
)

RESULT_SCHEMA_VERSION = "0.2"

_COMPOSE_ROLE_ORDER = {
    "environment-compose": 0,
    "implementation-compose": 1,
    "variant-compose": 2,
    "scenario-compose": 3,
}


@dataclass(frozen=True)
class RunPaths:
    result_dir: Path
    run_id: str


def run_benchmark_set(config: RunConfig, root_dir: Path) -> Path:
    paths = _run_set_paths(config, root_dir)
    paths.result_dir.mkdir(parents=True, exist_ok=False)
    run_log = paths.result_dir / "run.log"
    trial_count = int(config.measurement_protocol_config["trials"])
    trial_config = _single_trial_config(config)

    with run_log.open("a") as log:
        _log(log, f"Run set ID: {paths.run_id}")
        _validate_paths(trial_config)
        source = read_git_provenance(root_dir)
        manifest = build_resolved_manifest(trial_config, paths.run_id, source)
        validate_resolved_manifest(manifest, root_dir)
        write_json(paths.result_dir / "resolved-manifest.json", manifest)
        manifest_digest = str(manifest["manifest_digest"])
        cohort = manifest["cohort"]
        assert isinstance(cohort, dict)
        cohort_fingerprint = str(cohort["fingerprint"])
        compose_files = _compose_files(manifest, root_dir)
        started_at = _utc_timestamp()

        try:
            _log(log, "Cleaning previous containers...")
            _compose(compose_files, ["down", "-v", "--remove-orphans"], log)
            write_json(
                paths.result_dir / "metadata.json",
                _metadata(
                    trial_config,
                    paths.run_id,
                    manifest_digest,
                    cohort_fingerprint,
                ),
            )
            _log(log, "Measuring shared build...")
            build = _measure_build(trial_config, log)
            write_json(paths.result_dir / "build.json", build)

            trial_documents = []
            trial_references = []
            for index in range(1, trial_count + 1):
                trial_id = f"trial-{index:02d}"
                trial_dir = paths.result_dir / "trials" / f"{index:02d}"
                _log(log, f"Running {trial_id} ({index}/{trial_count})...")
                trial = _execute_trial(
                    trial_config,
                    root_dir,
                    compose_files,
                    paths.run_id,
                    trial_id,
                    index,
                    trial_dir,
                    manifest_digest,
                    cohort_fingerprint,
                    build,
                )
                trial_documents.append(trial)
                trial_references.append(
                    {
                        "trial_id": trial_id,
                        "index": index,
                        "status": trial["status"],
                        "path": f"trials/{index:02d}/trial.json",
                        "sha256": sha256_file(trial_dir / "trial.json"),
                    }
                )

            run_set = {
                "schema_version": "1.0",
                "run_set_id": paths.run_id,
                "run_id": paths.run_id,
                "status": "complete",
                "started_at": started_at,
                "finished_at": _utc_timestamp(),
                "manifest_digest": manifest_digest,
                "cohort_fingerprint": cohort_fingerprint,
                "expected_trials": trial_count,
                "trials": trial_references,
                "summary": summarize_trials(trial_documents),
            }
            validate_evidence_document(run_set, "run-set", root_dir)
            write_json(paths.result_dir / "run-set.json", run_set)
            validate_run_set_evidence(paths.result_dir, root_dir)
            _log(log, f"Run set written to: {paths.result_dir}")
            return paths.result_dir
        finally:
            _compose(compose_files, ["down", "-v", "--remove-orphans"], log)


def _execute_trial(
    config: RunConfig,
    root_dir: Path,
    compose_files: list[Path],
    run_set_id: str,
    trial_id: str,
    trial_index: int,
    trial_dir: Path,
    manifest_digest: str,
    cohort_fingerprint: str,
    build: dict[str, object],
) -> dict[str, object]:
    trial_dir.mkdir(parents=True, exist_ok=False)
    trial_log_path = trial_dir / "run.log"
    started_at = _utc_timestamp()
    cleaned = False
    with trial_log_path.open("a") as log:
        try:
            _compose(compose_files, ["down", "-v", "--remove-orphans"], log)
            metadata = _metadata(config, trial_id, manifest_digest, cohort_fingerprint)
            metadata["run_set_id"] = run_set_id
            metadata["trial_index"] = trial_index
            write_json(trial_dir / "metadata.json", metadata)

            startup = _measure_startup(compose_files, config, log)
            write_json(trial_dir / "startup.json", startup)
            k6_summary_path = trial_dir / "k6-summary.json"
            docker_stats = None
            if _load_enabled(config):
                _run_k6(
                    root_dir,
                    config,
                    str(config.load.get("warmup_duration", "10s")),
                    trial_dir / "k6-warmup-summary.json",
                    log,
                )
                docker_stats = _run_k6_with_docker_stats(
                    root_dir,
                    config,
                    str(config.load.get("test_duration", "30s")),
                    k6_summary_path,
                    log,
                )
            else:
                skipped = {"skipped": True, "reason": "load disabled for scenario"}
                write_json(trial_dir / "k6-warmup-summary.json", skipped)
                write_json(k6_summary_path, skipped)

            if docker_stats is None:
                docker_stats = _docker_stats(log)
            write_json(trial_dir / "docker-stats.json", docker_stats)
            samples = docker_stats.get("samples", [])
            if not isinstance(samples, list):
                samples = []
            interval = float(docker_stats.get("sample_interval_seconds", 1))
            time_series = build_compact_time_series(trial_id, interval, samples)
            validate_evidence_document(time_series, "time-series", root_dir)
            time_series_path = trial_dir / "time-series.json"
            write_json(time_series_path, time_series)

            result = _result_document(
                config,
                trial_id,
                manifest_digest,
                cohort_fingerprint,
                metadata["environment"],
                build,
                startup,
                read_json(k6_summary_path),
                docker_stats,
            )
            result["run_set_id"] = run_set_id
            result["trial_index"] = trial_index
            write_json(trial_dir / "result.json", result)
            status, invalid_reasons = _trial_validity(read_json(k6_summary_path))
            _write_target_log(compose_files, trial_dir, log)
            _compose(compose_files, ["down", "-v", "--remove-orphans"], log)
            cleaned = True
            artifact_manifest = build_artifact_manifest(trial_id, trial_dir)
            validate_evidence_document(
                artifact_manifest, "artifact-manifest", root_dir
            )
            artifact_manifest_path = trial_dir / "artifact-manifest.json"
            write_json(artifact_manifest_path, artifact_manifest)
            trial_document = {
                "schema_version": "1.0",
                "trial_id": trial_id,
                "run_id": run_set_id,
                "status": status,
                "started_at": started_at,
                "finished_at": _utc_timestamp(),
                "manifest_digest": manifest_digest,
                "cohort_fingerprint": cohort_fingerprint,
                "summary": build_trial_summary(result),
                "time_series": {
                    "path": "time-series.json",
                    "sha256": sha256_file(time_series_path),
                },
                "artifact_manifest": {
                    "path": "artifact-manifest.json",
                    "sha256": sha256_file(artifact_manifest_path),
                },
            }
            if invalid_reasons:
                trial_document["invalid_reasons"] = invalid_reasons
            validate_evidence_document(trial_document, "trial", root_dir)
            write_json(trial_dir / "trial.json", trial_document)
            return {**trial_document, "result": result}
        finally:
            if not cleaned:
                _compose(
                    compose_files,
                    ["down", "-v", "--remove-orphans"],
                    log,
                )


def _single_trial_config(config: RunConfig) -> RunConfig:
    if config.measurement_protocol_config["evidence_family"] != "lifecycle":
        return config
    return replace(config, startup={**config.startup, "iterations": 1})


def _trial_validity(k6_summary: dict[str, object]) -> tuple[str, list[str]]:
    if k6_summary.get("skipped") is True:
        return "valid", []
    metrics = k6_summary.get("metrics", {})
    if not isinstance(metrics, dict):
        return "invalid", ["k6 summary is missing metrics"]
    checks = metrics.get("checks", {})
    if not isinstance(checks, dict):
        return "invalid", ["k6 summary is missing check results"]
    fails = checks.get("fails")
    if not isinstance(fails, (int, float)):
        return "invalid", ["k6 summary is missing check failure count"]
    if fails > 0:
        return "invalid", [f"k6 reported {int(fails)} failed checks"]
    return "valid", []


def run_benchmark(config: RunConfig, root_dir: Path) -> Path:
    paths = _run_paths(config, root_dir)
    paths.result_dir.mkdir(parents=True, exist_ok=True)
    run_log = paths.result_dir / "run.log"

    with run_log.open("a") as log:
        _log(log, f"Run ID: {paths.run_id}")
        _validate_paths(config)

        source = read_git_provenance(root_dir)
        manifest = build_resolved_manifest(config, paths.run_id, source)
        validate_resolved_manifest(manifest, root_dir)
        write_json(paths.result_dir / "resolved-manifest.json", manifest)
        manifest_digest = str(manifest["manifest_digest"])
        cohort = manifest["cohort"]
        assert isinstance(cohort, dict)
        cohort_fingerprint = str(cohort["fingerprint"])
        _log(log, f"manifest_digest: {manifest_digest}")
        _log(log, f"cohort_fingerprint: {cohort_fingerprint}")

        compose_files = _compose_files(manifest, root_dir)

        try:
            _log(log, "Cleaning previous containers...")
            _compose(compose_files, ["down", "-v", "--remove-orphans"], log, allow_failure=True)

            metadata = _metadata(
                config,
                paths.run_id,
                manifest_digest,
                cohort_fingerprint,
            )
            write_json(paths.result_dir / "metadata.json", metadata)

            _log(log, "Measuring build...")
            build = _measure_build(config, log)
            write_json(paths.result_dir / "build.json", build)

            _log(log, "Measuring startup...")
            startup = _measure_startup(compose_files, config, log)
            write_json(paths.result_dir / "startup.json", startup)

            k6_summary_path = paths.result_dir / "k6-summary.json"
            docker_stats = None
            if _load_enabled(config):
                _log(log, "Running warmup...")
                _run_k6(
                    root_dir,
                    config,
                    str(config.load.get("warmup_duration", "10s")),
                    paths.result_dir / "k6-warmup-summary.json",
                    log,
                )

                _log(log, "Running benchmark...")
                docker_stats = _run_k6_with_docker_stats(
                    root_dir,
                    config,
                    str(config.load.get("test_duration", "30s")),
                    k6_summary_path,
                    log,
                )
            else:
                skipped = {"skipped": True, "reason": "load disabled for scenario"}
                write_json(paths.result_dir / "k6-warmup-summary.json", skipped)
                write_json(k6_summary_path, skipped)

            if docker_stats is None:
                _log(log, "Collecting Docker stats...")
                docker_stats = _docker_stats(log)
            write_json(paths.result_dir / "docker-stats.json", docker_stats)

            result = _result_document(
                config,
                paths.run_id,
                manifest_digest,
                cohort_fingerprint,
                metadata["environment"],
                build,
                startup,
                read_json(k6_summary_path),
                docker_stats,
            )
            write_json(paths.result_dir / "result.json", result)

            _log(log, f"Result written to: {paths.result_dir}")
            return paths.result_dir
        finally:
            _write_target_log(compose_files, paths.result_dir, log)
            _compose(compose_files, ["down", "-v", "--remove-orphans"], log, allow_failure=True)


def _run_paths(config: RunConfig, root_dir: Path) -> RunPaths:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    run_id = (
        f"{timestamp}_{config.language}_{config.framework}_{config.variant}_{config.scenario}"
    )
    result_dir = root_dir / "results" / Path(*config.result_prefix) / run_id
    return RunPaths(result_dir=result_dir, run_id=run_id)


def _run_set_paths(config: RunConfig, root_dir: Path) -> RunPaths:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    run_set_id = (
        f"{timestamp}_{config.language}_{config.framework}_{config.variant}_"
        f"{config.scenario}_run-set"
    )
    result_dir = root_dir / "results" / Path(*config.result_prefix) / run_set_id
    return RunPaths(result_dir=result_dir, run_id=run_set_id)


def _validate_paths(config: RunConfig) -> None:
    if not config.app_dir.is_dir():
        raise SystemExit(f"Implementation directory not found: {config.app_dir}")
    if not config.variant_file.is_file():
        raise SystemExit(f"Variant file not found: {config.variant_file}")
    if _load_enabled(config):
        _scenario_script(config)
    if shutil.which("docker") is None:
        raise SystemExit("docker is required.")


def _scenario_script(config: RunConfig) -> Path:
    script = (config.root_dir / str(config.load.get("script", ""))).resolve()
    scenario_dir = config.scenario_dir.resolve()
    try:
        script.relative_to(scenario_dir)
    except ValueError:
        raise SystemExit(f"Invalid scenario k6 script: {script}") from None
    if script.suffix != ".js" or not script.is_file():
        raise SystemExit(f"Invalid scenario k6 script: {script}")
    return script


def _compose_files(manifest: dict[str, object], root_dir: Path) -> list[Path]:
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Invalid compose assets in resolved manifest")

    compose_assets = []
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("role") not in _COMPOSE_ROLE_ORDER:
            continue
        relative_path = asset.get("path")
        if not isinstance(relative_path, str):
            raise ValueError("Invalid compose asset path in resolved manifest")
        path = root_dir / relative_path
        try:
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(root_dir.resolve(strict=True))
        except (FileNotFoundError, ValueError):
            raise ValueError(f"Invalid compose asset path: {relative_path}") from None
        if (
            resolved_path != path.absolute()
            or not path.is_file()
            or path.parent != root_dir / "infra"
            or not path.name.startswith("docker-compose.")
            or path.suffix not in {".yml", ".yaml"}
        ):
            raise ValueError(f"Invalid compose asset path: {relative_path}")
        compose_assets.append((str(asset["role"]), path))

    roles = [role for role, _path in compose_assets]
    if len(roles) != len(set(roles)):
        raise ValueError("Invalid duplicate compose asset role in resolved manifest")
    required_roles = {"environment-compose", "implementation-compose"}
    if not required_roles.issubset(roles):
        raise ValueError("Invalid compose assets in resolved manifest: required roles missing")

    compose_assets.sort(key=lambda item: _COMPOSE_ROLE_ORDER[item[0]])
    return [path for _role, path in compose_assets]


def _metadata(
    config: RunConfig,
    run_id: str,
    manifest_digest: str,
    cohort_fingerprint: str,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "manifest_digest": manifest_digest,
        "cohort_fingerprint": cohort_fingerprint,
        "project": "hello-realworld-bench",
        "scenario": config.scenario,
        "implementation": config.implementation,
        "variant": config.variant,
        "runtime": _runtime(config),
        "environment": environment_metadata(),
    }


def _runtime(config: RunConfig) -> dict[str, object]:
    runtime = dict(config.runtime)
    runtime["language"] = config.language
    runtime["framework"] = config.framework
    return runtime


def _measure_build(config: RunConfig, log) -> dict[str, object]:
    java_version = str(config.runtime.get("java_version", "25"))
    clean_build_ms = _measure_ms(
        [
            "docker",
            "run",
            "--rm",
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "GRADLE_USER_HOME=/workspace/.gradle-cache",
            "-v",
            f"{config.app_dir}:/workspace",
            "-w",
            "/workspace",
            f"eclipse-temurin:{java_version}-jdk",
            "./gradlew",
            "clean",
            "build",
            "--no-daemon",
        ],
        log,
    )
    docker_build_ms = _measure_ms(
        ["docker", "build", "-t", config.image_tag, str(config.app_dir)],
        log,
    )
    image_size = run(
        ["docker", "image", "inspect", config.image_tag, "--format", "{{.Size}}"],
        capture=True,
    ).stdout.strip()
    return {
        "clean_build_ms": clean_build_ms,
        "docker_build_ms": docker_build_ms,
        "image_size_mb": round(int(image_size) / 1024 / 1024, 2),
        "cache": {
            "gradle_user_home": "implementation-local .gradle-cache",
            "gradle_dependency_cache": "persistent",
            "docker_build_cache": "enabled",
            "docker_build_input": "prebuilt application artifact",
        },
    }


def _measure_startup(compose_files: list[Path], config: RunConfig, log) -> dict[str, object]:
    iterations = max(1, int(config.startup.get("iterations", 1)))
    samples = []

    for iteration in range(1, iterations + 1):
        _log(log, f"Startup iteration {iteration}/{iterations}...")
        _compose(compose_files, ["down", "-v", "--remove-orphans"], log, allow_failure=True)
        sample = _measure_startup_once(compose_files, config, log)
        sample["iteration"] = iteration
        samples.append(sample)

    summary = summarize_startup_samples(samples)
    first_sample = samples[0]
    return {
        "dependency_ready_ms": first_sample["dependency_ready_ms"],
        "ready_ms": first_sample["ready_ms"],
        "first_request_ms": first_sample["first_request_ms"],
        "iterations": iterations,
        "samples": samples,
        "summary": summary,
    }


def _measure_startup_once(
    compose_files: list[Path],
    config: RunConfig,
    log,
) -> dict[str, object]:
    base_url = str(config.target.get("base_url", "http://localhost:8080"))
    endpoint = str(config.target.get("startup_path") or config.target.get("endpoint", "/ping"))
    poll_interval = float(config.startup.get("poll_interval_seconds", 1))
    timeout_seconds = float(config.startup.get("timeout_seconds", 120))
    target_url = f"{base_url}{endpoint}"
    dependency_ready_ms = _prestart_dependency_services(compose_files, config, log)
    start = time.perf_counter()
    _compose(compose_files, ["up", "-d", "--no-deps", "target"], log)

    deadline = start + timeout_seconds
    while time.perf_counter() < deadline:
        first_request_ms = _http_success_latency_ms(target_url)
        if first_request_ms is not None:
            ready_ms = round((time.perf_counter() - start) * 1000)
            break
        time.sleep(poll_interval)
    else:
        raise SystemExit(
            f"Target endpoint did not return 200 within {timeout_seconds:g} seconds: {target_url}"
        )

    return {
        "dependency_ready_ms": dependency_ready_ms,
        "ready_ms": ready_ms,
        "first_request_ms": first_request_ms,
    }


def _prestart_dependency_services(
    compose_files: list[Path],
    config: RunConfig,
    log,
) -> int:
    services = _dependency_services(config)
    if not services:
        return 0

    _log(log, f"Prestarting dependency services: {', '.join(services)}")
    start = time.perf_counter()
    _compose(compose_files, ["up", "-d", "--wait", *services], log)
    return round((time.perf_counter() - start) * 1000)


def _dependency_services(config: RunConfig) -> list[str]:
    services = config.scenario_config.get("services", {})
    if not isinstance(services, dict):
        return []

    service_names = {
        "postgres": "postgres",
        "redis": "redis",
        "kafka": "kafka",
        "mock_upstream": "mock-upstream",
    }
    return [
        service_name
        for key, service_name in service_names.items()
        if services.get(key) is True
    ]


def _run_k6(root_dir: Path, config: RunConfig, duration: str, summary_path: Path, log) -> None:
    script = _scenario_script(config)
    base_url = str(config.target.get("base_url", "http://localhost:8080"))
    vus = str(config.load.get("vus", 50))
    if shutil.which("k6"):
        env = os.environ.copy()
        env.update(
            {
                "BASE_URL": base_url,
                "VUS": vus,
                "DURATION": duration,
            }
        )
        _run_logged(["k6", "run", "--summary-export", str(summary_path), str(script)], log, env)
        return

    _run_logged(
        [
            "docker",
            "run",
            "--rm",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-e",
            "BASE_URL=http://host.docker.internal:8080",
            "-e",
            f"VUS={vus}",
            "-e",
            f"DURATION={duration}",
            "-v",
            f"{root_dir}:/work",
            "-w",
            "/work",
            "grafana/k6:0.54.0",
            "run",
            "--summary-export",
            _container_path(root_dir, summary_path),
            _container_path(root_dir, script),
        ],
        log,
    )


def _run_k6_with_docker_stats(
    root_dir: Path,
    config: RunConfig,
    duration: str,
    summary_path: Path,
    log,
) -> dict[str, object]:
    samples: list[dict[str, object]] = []
    stop_event = threading.Event()
    interval_seconds = float(config.load.get("docker_stats_interval_seconds", 1))
    sample_start = time.perf_counter()
    sampler = threading.Thread(
        target=_sample_docker_stats,
        args=(samples, stop_event, interval_seconds, sample_start),
        daemon=True,
    )

    _log(log, "Sampling Docker stats during benchmark...")
    sampler.start()
    try:
        _run_k6(root_dir, config, duration, summary_path, log)
    finally:
        stop_event.set()
        sampler.join(timeout=interval_seconds + 1)

    if not samples:
        fallback = _docker_stats_sample()
        if fallback is not None:
            fallback["elapsed_ms"] = round((time.perf_counter() - sample_start) * 1000)
            samples.append(fallback)

    return {
        "sample_interval_seconds": interval_seconds,
        "samples": samples,
    }


def _sample_docker_stats(
    samples: list[dict[str, object]],
    stop_event: threading.Event,
    interval_seconds: float,
    sample_start: float,
) -> None:
    process = subprocess.Popen(
        ["docker", "stats", "--format", "{{json .}}", "hrw-target"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    interval_ms = max(1, round(interval_seconds * 1000))
    next_elapsed_ms = 0
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if stop_event.is_set():
                break
            sample = _docker_stats_json_line(line)
            if sample is None:
                continue
            elapsed_ms = round((time.perf_counter() - sample_start) * 1000)
            if elapsed_ms < next_elapsed_ms:
                continue
            sample["elapsed_ms"] = elapsed_ms
            samples.append(sample)
            while next_elapsed_ms <= elapsed_ms:
                next_elapsed_ms += interval_ms
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _docker_stats_json_line(line: str) -> dict[str, object] | None:
    start = line.find("{")
    end = line.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(line[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _docker_stats_sample() -> dict[str, object] | None:
    try:
        completed = run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", "hrw-target"],
            capture=True,
        )
    except Exception:
        return None

    output = completed.stdout.strip()
    if not output:
        return None
    return json.loads(output.splitlines()[0])


def _docker_stats(log) -> dict[str, object]:
    completed = _run_logged(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", "hrw-target"],
        log,
        capture=True,
    )
    output = completed.stdout.strip()
    if not output:
        return {"error": "docker stats returned no data"}
    return json.loads(output.splitlines()[0])


def _load_enabled(config: RunConfig) -> bool:
    return config.load.get("enabled", True) is not False


def _result_document(
    config: RunConfig,
    run_id: str,
    manifest_digest: str,
    cohort_fingerprint: str,
    environment: dict[str, object],
    build: dict[str, object],
    startup: dict[str, object],
    k6_summary: dict[str, object],
    docker_stats: dict[str, object],
) -> dict[str, object]:
    runtime_metrics = k6_runtime_metrics(k6_summary)
    runtime_metrics.update(docker_resource_metrics(docker_stats))

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "manifest_digest": manifest_digest,
        "cohort_fingerprint": cohort_fingerprint,
        "project": "hello-realworld-bench",
        "scenario": config.scenario,
        "implementation": config.implementation,
        "variant": config.variant,
        "runtime": _runtime(config),
        "environment": environment,
        "build": {
            "clean_build_ms": build.get("clean_build_ms"),
            "docker_build_ms": build.get("docker_build_ms"),
            "image_size_mb": build.get("image_size_mb"),
            "cache": build.get("cache"),
        },
        "startup": {
            "dependency_ready_ms": startup.get("dependency_ready_ms"),
            "ready_ms": startup.get("ready_ms"),
            "first_request_ms": startup.get("first_request_ms"),
            "iterations": startup.get("iterations"),
            "summary": startup.get("summary"),
        },
        "runtime_metrics": runtime_metrics,
    }


def _measure_ms(args: list[str], log) -> int:
    start = time.perf_counter()
    _run_logged(args, log)
    return round((time.perf_counter() - start) * 1000)


def _compose(
    compose_files: list[Path],
    args: list[str],
    log,
    allow_failure: bool = False,
) -> None:
    command = ["docker", "compose"]
    for compose_file in compose_files:
        command.extend(["-f", str(compose_file)])
    command.extend(args)
    try:
        _run_logged(command, log)
    except Exception:
        if not allow_failure:
            raise


def _write_target_log(compose_files: list[Path], result_dir: Path, log) -> None:
    command = ["docker", "compose"]
    for compose_file in compose_files:
        command.extend(["-f", str(compose_file)])
    command.extend(["logs", "target"])
    try:
        completed = _run_logged(command, log, capture=True)
        (result_dir / "target.log").write_text(completed.stdout)
    except Exception:
        pass


def _run_logged(args: list[str], log, env: dict[str, str] | None = None, capture: bool = False):
    _log(log, "$ " + " ".join(args))
    completed = run(args, env=env, capture=capture)
    if capture and completed.stdout:
        log.write(completed.stdout)
        log.flush()
    return completed


def _http_success_latency_ms(url: str) -> int | None:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            response.read()
            if response.status == 200:
                return round((time.perf_counter() - start) * 1000)
            return None
    except Exception:
        return None


def _container_path(root_dir: Path, path: Path) -> str:
    return "/work/" + str(path.relative_to(root_dir))


def _log(log, message: str) -> None:
    print(message, flush=True)
    log.write(message + "\n")
    log.flush()


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
