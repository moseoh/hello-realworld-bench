from __future__ import annotations

from math import ceil
from typing import Any


def normalize_stats_sample(
    snapshot: dict[str, Any],
    namespace: str,
    elapsed_ms: int,
    target_memory_limit_bytes: int = 1024**3,
) -> dict[str, object]:
    node = snapshot["node"]
    node_cpu_nano = int(node["cpu"].get("usageNanoCores", 0))
    node_memory = int(node["memory"].get("workingSetBytes", 0))
    roles = {
        "target": {"cpu": 0, "memory": 0},
        "load_generator": {"cpu": 0, "memory": 0},
        "dependency": {"cpu": 0, "memory": 0},
    }
    benchmark_cpu = 0
    benchmark_memory = 0
    for pod in snapshot.get("pods", []):
        pod_ref = pod.get("podRef", {})
        if pod_ref.get("namespace") != namespace:
            continue
        name = str(pod_ref.get("name", ""))
        if name == "target":
            role = "target"
        elif name.startswith("k6-"):
            role = "load_generator"
        else:
            role = "dependency"
        for container in pod.get("containers", []):
            cpu = int(container.get("cpu", {}).get("usageNanoCores", 0))
            memory = int(container.get("memory", {}).get("workingSetBytes", 0))
            roles[role]["cpu"] += cpu
            roles[role]["memory"] += memory
            benchmark_cpu += cpu
            benchmark_memory += memory

    return {
        "elapsed_ms": elapsed_ms,
        "source_time": str(node["cpu"]["time"]),
        "target_cpu_percent": _cpu_percent(roles["target"]["cpu"]),
        "target_memory_bytes": roles["target"]["memory"],
        "target_memory_percent": round(
            roles["target"]["memory"] / target_memory_limit_bytes * 100,
            4,
        ),
        "load_generator_cpu_percent": _cpu_percent(
            roles["load_generator"]["cpu"]
        ),
        "load_generator_memory_bytes": roles["load_generator"]["memory"],
        "dependency_cpu_percent": _cpu_percent(roles["dependency"]["cpu"]),
        "dependency_memory_bytes": roles["dependency"]["memory"],
        "host_cpu_percent": _cpu_percent(node_cpu_nano),
        "host_memory_bytes": node_memory,
        "background_cpu_millicores": round(
            max(0, node_cpu_nano - benchmark_cpu) / 1_000_000,
            4,
        ),
        "background_memory_bytes": max(0, node_memory - benchmark_memory),
    }


def validate_stats_series(
    samples: list[dict[str, object]],
    measured_seconds: int,
    validity: dict[str, Any],
) -> dict[str, object]:
    interval = int(validity["stats_sample_interval_seconds"])
    expected = max(1, ceil(measured_seconds / interval))
    measured_samples = [
        sample
        for sample in samples
        if float(sample.get("load_generator_cpu_percent", 0)) > 0
    ]
    unique_source_times = {sample.get("source_time") for sample in measured_samples}
    unique_source_times.discard(None)
    coverage = min(1.0, len(unique_source_times) / expected)
    max_cpu = max(
        (
            float(sample.get("background_cpu_millicores", 0))
            for sample in measured_samples
        ),
        default=0,
    )
    max_memory = max(
        (int(sample.get("background_memory_bytes", 0)) for sample in measured_samples),
        default=0,
    )
    reasons = []
    if coverage < float(validity["min_sample_coverage_ratio"]):
        reasons.append(
            f"stats coverage {coverage:.3f} is below "
            f"{validity['min_sample_coverage_ratio']}"
        )
    if max_cpu > float(validity["max_background_cpu_millicores"]):
        reasons.append(
            f"background CPU {max_cpu:g}m exceeds "
            f"{validity['max_background_cpu_millicores']}m"
        )
    if max_memory > int(validity["max_background_memory_bytes"]):
        reasons.append(
            f"background memory {max_memory} exceeds "
            f"{validity['max_background_memory_bytes']} bytes"
        )
    return {
        "status": "valid" if not reasons else "invalid",
        "reasons": reasons,
        "expected_samples": expected,
        "observed_samples": len(unique_source_times),
        "coverage_ratio": round(coverage, 4),
        "max_background_cpu_millicores": max_cpu,
        "max_background_memory_bytes": max_memory,
    }


def _cpu_percent(usage_nano_cores: int) -> float:
    return round(usage_nano_cores / 10_000_000, 4)
