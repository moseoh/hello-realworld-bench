from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .commands import run
from .config import RunConfig
from .results import (
    docker_resource_metrics,
    environment_metadata,
    k6_runtime_metrics,
    read_json,
    summarize_startup_samples,
    write_json,
)

RESULT_SCHEMA_VERSION = "0.1"


@dataclass(frozen=True)
class RunPaths:
    result_dir: Path
    run_id: str


def run_benchmark(config: RunConfig, root_dir: Path) -> Path:
    paths = _run_paths(config, root_dir)
    paths.result_dir.mkdir(parents=True, exist_ok=True)
    run_log = paths.result_dir / "run.log"

    with run_log.open("a") as log:
        _log(log, f"Run ID: {paths.run_id}")
        _validate_paths(config)

        compose_files = [
            root_dir / "infra" / "docker-compose.base.yml",
            root_dir / "infra" / f"docker-compose.{config.compose_profile}.yml",
        ]

        try:
            _log(log, "Cleaning previous containers...")
            _compose(compose_files, ["down", "-v", "--remove-orphans"], log, allow_failure=True)

            metadata = _metadata(config, paths.run_id)
            write_json(paths.result_dir / "metadata.json", metadata)

            _log(log, "Measuring build...")
            build = _measure_build(config, log)
            write_json(paths.result_dir / "build.json", build)

            _log(log, "Measuring startup...")
            startup = _measure_startup(compose_files, config, log)
            write_json(paths.result_dir / "startup.json", startup)

            k6_summary_path = paths.result_dir / "k6-summary.json"
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
                _run_k6(
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

            _log(log, "Collecting Docker stats...")
            docker_stats = _docker_stats(log)
            write_json(paths.result_dir / "docker-stats.json", docker_stats)

            result = _result_document(
                config,
                paths.run_id,
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


def _validate_paths(config: RunConfig) -> None:
    if not config.app_dir.is_dir():
        raise SystemExit(f"Implementation directory not found: {config.app_dir}")
    if not config.variant_file.is_file():
        raise SystemExit(f"Variant file not found: {config.variant_file}")
    if _load_enabled(config):
        script = config.root_dir / str(config.load.get("script", ""))
        if not script.is_file():
            raise SystemExit(f"Scenario k6 script not found: {script}")
    if shutil.which("docker") is None:
        raise SystemExit("docker is required.")


def _metadata(config: RunConfig, run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "project": "hello-realworld-bench",
        "scenario": config.scenario,
        "implementation": config.implementation,
        "variant": config.variant,
        "runtime": _runtime(config),
        "environment": environment_metadata(),
    }


def _runtime(config: RunConfig) -> dict[str, object]:
    runtime = dict(config.runtime)
    runtime.setdefault("language", config.language)
    runtime.setdefault("framework", config.framework)
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
    endpoint = str(config.target.get("endpoint", "/ping"))
    poll_interval = float(config.startup.get("poll_interval_seconds", 1))
    timeout_seconds = float(config.startup.get("timeout_seconds", 120))
    target_url = f"{base_url}{endpoint}"
    start = time.perf_counter()
    _compose(compose_files, ["up", "-d", "target"], log)

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
        "ready_ms": ready_ms,
        "first_request_ms": first_request_ms,
    }


def _run_k6(root_dir: Path, config: RunConfig, duration: str, summary_path: Path, log) -> None:
    script = config.root_dir / str(config.load.get("script"))
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
