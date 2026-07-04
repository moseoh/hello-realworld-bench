from __future__ import annotations

import json
import platform
import subprocess
from math import ceil
from pathlib import Path
from statistics import median
from typing import Any


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def environment_metadata() -> dict[str, Any]:
    return {
        "os": platform.system() or "unknown",
        "cpu": _cpu_name(),
        "memory_gb": _memory_gb(),
        "docker": True,
        "load_generator": "same-host",
    }


def _cpu_name() -> str:
    for command in (
        ["sysctl", "-n", "machdep.cpu.brand_string"],
        ["lscpu"],
    ):
        try:
            completed = subprocess.run(command, check=True, text=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        output = completed.stdout.strip()
        if command[0] == "lscpu":
            for line in output.splitlines():
                if line.startswith("Model name:"):
                    return line.split(":", 1)[1].strip()
        if output:
            return output
    return "unknown"


def _memory_gb() -> str:
    try:
        completed = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=True,
            text=True,
            capture_output=True,
        )
        return f"{int(completed.stdout.strip()) / 1024 / 1024 / 1024:.2f}"
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return "unknown"


def k6_runtime_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("skipped") is True:
        return {}

    metrics = summary.get("metrics", {})
    http_reqs = metrics.get("http_reqs", {})
    duration = metrics.get("http_req_duration", {})
    failed = metrics.get("http_req_failed", {})

    return {
        "rps": http_reqs.get("rate"),
        "p50_ms": duration.get("med"),
        "p95_ms": duration.get("p(95)"),
        "p99_ms": duration.get("p(99)"),
        "error_rate": _first_present(failed, "rate", "value"),
    }


def docker_resource_metrics(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "cpu_percent": _percent(stats.get("CPUPerc")),
        "memory_usage": stats.get("MemUsage"),
        "memory_percent": _percent(stats.get("MemPerc")),
    }


def summarize_startup_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ready_ms": _summarize_numeric_values(samples, "ready_ms"),
        "first_request_ms": _summarize_numeric_values(samples, "first_request_ms"),
    }


def _percent(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip().removesuffix("%")
    try:
        return float(stripped)
    except ValueError:
        return None


def _first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _summarize_numeric_values(samples: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [sample[key] for sample in samples if isinstance(sample.get(key), (int, float))]
    if not values:
        return {
            "min": None,
            "median": None,
            "p95": None,
            "max": None,
        }

    sorted_values = sorted(values)
    p95_index = max(0, ceil(len(sorted_values) * 0.95) - 1)
    return {
        "min": sorted_values[0],
        "median": median(sorted_values),
        "p95": sorted_values[p95_index],
        "max": sorted_values[-1],
    }
