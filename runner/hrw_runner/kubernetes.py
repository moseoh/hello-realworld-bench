from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .commands import run


@dataclass(frozen=True)
class Kubectl:
    context: str
    cwd: Path

    def current_context(self) -> str:
        return run(
            [
                "kubectl",
                "config",
                "get-contexts",
                self.context,
                "--no-headers",
                "-o",
                "name",
            ],
            cwd=self.cwd,
            capture=True,
        ).stdout.strip()

    def json(self, arguments: list[str]) -> dict[str, Any]:
        output_arguments = (
            arguments
            if arguments[:2] == ["get", "--raw"]
            else [*arguments, "-o", "json"]
        )
        completed = run(
            ["kubectl", "--context", self.context, *output_arguments],
            cwd=self.cwd,
            capture=True,
        )
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError("kubectl JSON output must be an object")
        return value

    def apply(self, documents: list[dict[str, Any]]) -> None:
        subprocess.run(
            ["kubectl", "--context", self.context, "apply", "-f", "-"],
            cwd=self.cwd,
            input=yaml.safe_dump_all(documents, sort_keys=False),
            check=True,
            text=True,
        )

    def command(self, arguments: list[str], *, capture: bool = False) -> str:
        completed = run(
            ["kubectl", "--context", self.context, *arguments],
            cwd=self.cwd,
            capture=capture,
        )
        return completed.stdout if capture else ""


def evaluate_preflight(
    client: Kubectl,
    environment: dict[str, Any],
) -> dict[str, object]:
    cluster_contract = environment["cluster"]
    validity = environment["validity"]
    expected_context = str(cluster_contract["context"])
    actual_context = client.current_context()
    if actual_context != expected_context:
        return {
            "status": "invalid",
            "reasons": [
                f"expected kube context {expected_context}, got {actual_context}"
            ],
            "cluster": {"context": actual_context},
        }

    node_name = str(cluster_contract["node_name"])
    node = client.json(["get", "node", node_name])
    version = client.json(["version"])
    namespaces = client.json(
        [
            "get",
            "namespaces",
            "-l",
            "app.kubernetes.io/part-of=hello-realworld-bench",
        ]
    )
    configz = client.json(
        ["get", "--raw", f"/api/v1/nodes/{node_name}/proxy/configz"]
    )
    stats = client.json(
        ["get", "--raw", f"/api/v1/nodes/{node_name}/proxy/stats/summary"]
    )

    status = node["status"]
    node_info = status["nodeInfo"]
    logical_cpus = int(status["capacity"]["cpu"])
    memory_bytes = _memory_bytes(status["capacity"]["memory"])
    ready = any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in status["conditions"]
    )
    cpu_manager_policy = configz["kubeletconfig"]["cpuManagerPolicy"]
    background_cpu = float(stats["node"]["cpu"]["usageNanoCores"]) / 1_000_000
    background_memory = int(stats["node"]["memory"]["workingSetBytes"])
    existing_namespaces = [
        item["metadata"]["name"] for item in namespaces.get("items", [])
    ]

    reasons = []
    if not ready:
        reasons.append(f"node {node_name} is not Ready")
    if node_info["architecture"] != cluster_contract["architecture"]:
        reasons.append(
            f"expected architecture {cluster_contract['architecture']}, "
            f"got {node_info['architecture']}"
        )
    if node_info["machineID"] != cluster_contract["machine_id"]:
        reasons.append(
            f"expected machine ID {cluster_contract['machine_id']}, "
            f"got {node_info['machineID']}"
        )
    if logical_cpus < int(cluster_contract["min_logical_cpus"]):
        reasons.append(
            f"expected at least {cluster_contract['min_logical_cpus']} logical CPUs, "
            f"got {logical_cpus}"
        )
    if memory_bytes < int(cluster_contract["min_memory_bytes"]):
        reasons.append(
            f"expected at least {cluster_contract['min_memory_bytes']} memory bytes, "
            f"got {memory_bytes}"
        )
    if cpu_manager_policy != cluster_contract["cpu_manager_policy"]:
        reasons.append(
            f"expected cpuManagerPolicy {cluster_contract['cpu_manager_policy']}, "
            f"got {cpu_manager_policy}"
        )
    if existing_namespaces:
        reasons.append(
            "existing benchmark namespace(s): " + ", ".join(existing_namespaces)
        )
    if background_cpu > float(validity["max_background_cpu_millicores"]):
        reasons.append(
            f"background CPU {background_cpu:g}m exceeds "
            f"{validity['max_background_cpu_millicores']}m"
        )
    if background_memory > int(validity["max_background_memory_bytes"]):
        reasons.append(
            f"background memory {background_memory} exceeds "
            f"{validity['max_background_memory_bytes']} bytes"
        )

    return {
        "status": "valid" if not reasons else "invalid",
        "reasons": reasons,
        "cluster": {
            "context": actual_context,
            "node_name": node_name,
            "architecture": node_info["architecture"],
            "machine_id": node_info["machineID"],
            "logical_cpus": logical_cpus,
            "memory_bytes": memory_bytes,
            "cpu_manager_policy": cpu_manager_policy,
            "kubernetes_version": version["serverVersion"]["gitVersion"],
            "kubelet_version": node_info["kubeletVersion"],
            "container_runtime": node_info["containerRuntimeVersion"],
            "kernel_version": node_info["kernelVersion"],
            "os_image": node_info["osImage"],
        },
        "background": {
            "cpu_millicores": background_cpu,
            "memory_working_set_bytes": background_memory,
        },
    }


def _memory_bytes(value: str) -> int:
    units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
    }
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            return int(value[: -len(suffix)]) * multiplier
    return int(value)
