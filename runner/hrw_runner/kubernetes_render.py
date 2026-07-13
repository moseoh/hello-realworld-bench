from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_IMMUTABLE_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")


def render_scenario_documents(
    template_path: Path,
    *,
    namespace: str,
    run_set_id: str,
    target_image: str,
    k6_image: str,
    java_tool_options: str,
    duration: str,
    vus: int,
    job_name: str,
    script: str,
    scenario_id: str = "ping-api",
    executor: str = "constant-vus",
    rate: int = 1,
    stages: str = "[]",
    pre_allocated_vus: int = 1,
    max_vus: int = 1,
    target_environment: Mapping[str, str],
) -> list[dict[str, Any]]:
    for name, value in (("namespace", namespace), ("run_set_id", run_set_id), ("job_name", job_name)):
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError(f"Invalid Kubernetes {name}: {value}")
    for name, image in (("target", target_image), ("k6", k6_image)):
        if not _IMMUTABLE_IMAGE.fullmatch(image):
            raise ValueError(f"Kubernetes {name} image must use an immutable sha256 digest")
    if not re.fullmatch(r"[1-9][0-9]*[smh]", duration):
        raise ValueError(f"Invalid k6 duration: {duration}")
    if not isinstance(vus, int) or vus < 1:
        raise ValueError(f"Invalid k6 VUs: {vus}")
    if not script.strip():
        raise ValueError("k6 script must not be empty")
    if executor not in {
        "constant-vus",
        "constant-arrival-rate",
        "ramping-arrival-rate",
    }:
        raise ValueError(f"Invalid k6 executor: {executor}")
    if any(
        not isinstance(value, int) or value < 1
        for value in (rate, pre_allocated_vus, max_vus)
    ):
        raise ValueError("Invalid k6 arrival-rate capacity")

    target_env = [
        {"name": "JAVA_TOOL_OPTIONS", "value": java_tool_options},
        *(
            {"name": name, "value": value}
            for name, value in sorted(target_environment.items())
        ),
    ]

    replacements: dict[str, object] = {
        "__NAMESPACE__": namespace,
        "__RUN_SET_ID__": run_set_id,
        "__SCENARIO_ID__": scenario_id,
        "__TARGET_IMAGE__": target_image,
        "__K6_IMAGE__": k6_image,
        "__TARGET_ENV__": target_env,
        "__K6_DURATION__": duration,
        "__K6_VUS__": str(vus),
        "__HRW_LOAD_EXECUTOR__": executor,
        "__HRW_LOAD_RATE__": str(rate),
        "__HRW_LOAD_STAGES__": stages,
        "__HRW_LOAD_PRE_ALLOCATED_VUS__": str(pre_allocated_vus),
        "__HRW_LOAD_MAX_VUS__": str(max_vus),
        "__K6_JOB_NAME__": job_name,
        "__K6_SCRIPT__": script,
    }
    documents = [
        _replace(document, replacements)
        for document in yaml.safe_load_all(template_path.read_text())
        if document is not None
    ]
    if any(_contains_placeholder(document) for document in documents):
        raise ValueError("Unresolved Kubernetes template placeholder")
    return documents


render_ping_documents = render_scenario_documents


def _replace(value: Any, replacements: dict[str, object]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            _replace(key, replacements): _replace(item, replacements)
            for key, item in value.items()
        }
    return value


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.fullmatch(r"__[A-Z0-9_]+__", value))
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_placeholder(key) or _contains_placeholder(item)
            for key, item in value.items()
        )
    return False
