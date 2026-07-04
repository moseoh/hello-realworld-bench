from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path
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
    metrics = summary.get("metrics", {})
    http_reqs = metrics.get("http_reqs", {})
    duration = metrics.get("http_req_duration", {})
    failed = metrics.get("http_req_failed", {})

    return {
        "rps": http_reqs.get("rate"),
        "p50_ms": duration.get("med"),
        "p95_ms": duration.get("p(95)"),
        "p99_ms": duration.get("p(99)"),
        "error_rate": failed.get("rate"),
        "cpu": None,
        "memory": None,
    }
