from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_IMMUTABLE_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")


class LifecycleEvidenceError(ValueError):
    pass


def image_digest_matches(expected: str, actual: str) -> bool:
    pattern = re.compile(r"sha256:[0-9a-f]{64}$")
    expected_digest = pattern.search(expected)
    actual_digest = pattern.search(actual.removeprefix("docker-pullable://"))
    return (
        expected_digest is not None
        and actual_digest is not None
        and expected_digest.group() == actual_digest.group()
    )


def build_prepull_evidence(
    pod: dict[str, Any], *, target_image: str, observer_image: str
) -> dict[str, Any]:
    expected = {"target": target_image, "observer": observer_image}
    containers = {
        str(container.get("name")): container
        for container in pod.get("spec", {}).get("containers", [])
    }
    statuses = {
        str(status.get("name")): status
        for status in pod.get("status", {}).get("containerStatuses", [])
    }
    reasons = []
    for name, image in expected.items():
        container = containers.get(name)
        if not isinstance(container, dict) or container.get("image") != image:
            reasons.append(f"pre-pull {name} image does not match {image}")
        elif container.get("imagePullPolicy") != "IfNotPresent":
            reasons.append(f"pre-pull {name} imagePullPolicy is not IfNotPresent")
        status = statuses.get(name)
        if not isinstance(status, dict):
            reasons.append(f"pre-pull container {name} has no status")
            continue
        image_id = str(status.get("imageID", ""))
        if not image_digest_matches(image, image_id):
            reasons.append(
                f"pre-pull imageID {image_id or 'missing'} does not match {image}"
            )
        if int(status.get("restartCount", 0)) != 0:
            reasons.append(f"pre-pull container {name} restarted")
    if set(containers) != set(expected):
        reasons.append("pre-pull Pod container set is not target and observer")
    if pod.get("status", {}).get("phase") != "Succeeded":
        reasons.append("pre-pull Pod did not succeed")
    return {
        "status": "invalid" if reasons else "valid",
        "reasons": reasons,
        "pod": pod,
    }


def render_lifecycle_documents(
    template_path: Path,
    *,
    namespace: str,
    run_set_id: str,
    target_image: str,
    observer_image: str,
    java_tool_options: str,
    timeout_seconds: int,
    poll_interval_ms: int,
    request_timeout_ms: int,
    target_environment: Mapping[str, str],
    target_resources: Mapping[str, str] | None = None,
    observer_resources: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    for name, value in (("namespace", namespace), ("run_set_id", run_set_id)):
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError(f"Invalid Kubernetes {name}: {value}")
    for name, image in (("target", target_image), ("observer", observer_image)):
        if not _IMMUTABLE_IMAGE.fullmatch(image):
            raise ValueError(
                f"Kubernetes {name} image must use an immutable sha256 digest"
            )
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("Lifecycle timeout must be a positive integer")
    if not isinstance(poll_interval_ms, int) or poll_interval_ms < 1:
        raise ValueError("Lifecycle poll interval must be a positive integer")
    if not isinstance(request_timeout_ms, int) or request_timeout_ms < 1:
        raise ValueError("Lifecycle request timeout must be a positive integer")

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
        "__TARGET_IMAGE__": target_image,
        "__OBSERVER_IMAGE__": observer_image,
        "__TARGET_ENV__": target_env,
        "__OBSERVER_SCRIPT__": _observer_script(
            timeout_seconds, poll_interval_ms, request_timeout_ms
        ),
        "__TARGET_RESOURCES__": _resource_pair(
            target_resources or {"cpu": "2", "memory": "1Gi"}
        ),
        "__OBSERVER_RESOURCES__": _resource_pair(
            observer_resources or {"cpu": "1", "memory": "256Mi"}
        ),
    }
    documents = [
        _replace(document, replacements)
        for document in yaml.safe_load_all(template_path.read_text())
        if document is not None
    ]
    if any(_contains_placeholder(document) for document in documents):
        raise ValueError("Unresolved Kubernetes lifecycle template placeholder")
    return documents


def build_lifecycle_measurement(
    target_pod: dict[str, Any],
    target_log: str,
    observer_log: str,
    *,
    timeout_seconds: int,
) -> dict[str, int | float | str]:
    container_started_at = _target_started_at(target_pod)
    container_started_ms = int(container_started_at.timestamp() * 1000)
    entrypoint_pre_exec_ms = _single_integer_marker(
        target_log, "HRW_ENTRYPOINT_PRE_EXEC_EPOCH_MS"
    )
    observer_ready_ms = _single_integer_marker(
        observer_log, "HRW_OBSERVER_READY_EPOCH_MS"
    )
    request_start_ms = _single_integer_marker(
        observer_log, "HRW_FIRST_REQUEST_START_EPOCH_MS"
    )
    first_success_ms = _single_integer_marker(
        observer_log, "HRW_FIRST_SUCCESS_EPOCH_MS"
    )
    first_request_ms = _single_number_marker(
        observer_log, "HRW_FIRST_REQUEST_DURATION_MS"
    )
    attempts = _single_integer_marker(observer_log, "HRW_ATTEMPTS")
    ready_ms = first_success_ms - entrypoint_pre_exec_ms
    if observer_ready_ms > entrypoint_pre_exec_ms:
        raise LifecycleEvidenceError("Observer was not armed before target pre-exec marker")
    if (
        entrypoint_pre_exec_ms < container_started_ms
        or entrypoint_pre_exec_ms - container_started_ms > 2000
    ):
        raise LifecycleEvidenceError("Entrypoint marker is inconsistent with CRI start")
    if request_start_ms > first_success_ms:
        raise LifecycleEvidenceError("First request starts after its completion")
    if ready_ms < 0:
        raise LifecycleEvidenceError("First success precedes target pre-exec marker")
    if ready_ms > timeout_seconds * 1000:
        raise LifecycleEvidenceError("First success exceeds the lifecycle timeout")
    if first_request_ms < 0 or first_request_ms > timeout_seconds * 1000:
        raise LifecycleEvidenceError("First request duration is outside the timeout")
    if abs((first_success_ms - request_start_ms) - first_request_ms) > 0.001:
        raise LifecycleEvidenceError(
            "First request duration is inconsistent with response completion"
        )
    if attempts < 1:
        raise LifecycleEvidenceError("Lifecycle observer recorded no attempts")
    return {
        "dependency_ready_ms": 0,
        "ready_ms": ready_ms,
        "entrypoint_pre_exec_to_first_valid_response_ms": ready_ms,
        "first_request_ms": first_request_ms,
        "container_started_at": container_started_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "container_started_epoch_ms": container_started_ms,
        "entrypoint_pre_exec_epoch_ms": entrypoint_pre_exec_ms,
        "observer_ready_epoch_ms": observer_ready_ms,
        "first_request_start_epoch_ms": request_start_ms,
        "first_success_epoch_ms": first_success_ms,
        "attempts": attempts,
        "iterations": 1,
        "start_boundary": "image-entrypoint-pre-exec",
        "completion_boundary": "first-valid-response-complete",
    }


def evaluate_lifecycle_boundaries(
    before: Mapping[str, object],
    after: Mapping[str, object],
    validity: Mapping[str, object],
) -> dict[str, object]:
    samples = [
        {"phase": phase, **sample}
        for phase, sample in (("before", before), ("after", after))
    ]
    reasons = []
    for sample in samples:
        cpu = float(sample.get("background_cpu_millicores", 0))
        memory = int(sample.get("background_memory_bytes", 0))
        if cpu > float(validity["max_background_cpu_millicores"]):
            reasons.append(
                f"{sample['phase']} background CPU {cpu:g}m exceeds "
                f"{validity['max_background_cpu_millicores']}m"
            )
        if memory > int(validity["max_background_memory_bytes"]):
            reasons.append(
                f"{sample['phase']} background memory {memory} exceeds "
                f"{validity['max_background_memory_bytes']} bytes"
            )
    return {
        "status": "invalid" if reasons else "valid",
        "reasons": reasons,
        "samples": samples,
    }


def validate_lifecycle_pod(
    pod: dict[str, Any], *, target_image: str, observer_image: str
) -> list[str]:
    reasons = []
    checks = (
        ("target", pod.get("status", {}).get("containerStatuses", []), target_image),
        (
            "observer",
            pod.get("status", {}).get("initContainerStatuses", []),
            observer_image,
        ),
    )
    for name, statuses, expected_image in checks:
        matches = [status for status in statuses if status.get("name") == name]
        if len(matches) != 1:
            reasons.append(f"{name} container status is missing")
            continue
        status = matches[0]
        image_id = str(status.get("imageID", ""))
        if not image_digest_matches(expected_image, image_id):
            reasons.append(
                f"{name} imageID {image_id or 'missing'} does not match "
                f"{expected_image}"
            )
        if int(status.get("restartCount", 0)) != 0:
            reasons.append(f"{name} restarted {status['restartCount']} time(s)")
        if not isinstance(status.get("state", {}).get("running"), dict):
            reasons.append(f"{name} was not running at first success")
        if _was_oom_killed(status):
            reasons.append(f"{name} was OOMKilled")
    return reasons


def _observer_script(
    timeout_seconds: int, poll_interval_ms: int, request_timeout_ms: int
) -> str:
    return f'''import http from "k6/http";
import {{ fail, sleep }} from "k6";

export const options = {{
  scenarios: {{
    observer: {{
      executor: "shared-iterations",
      vus: 1,
      iterations: 1,
      maxDuration: "{timeout_seconds + 30}s",
    }},
  }},
}};

export default function () {{
  const deadline = Date.now() + {timeout_seconds * 1000};
  let attempts = 0;
  console.log(`HRW_OBSERVER_READY_EPOCH_MS=${{Date.now()}}`);
  while (Date.now() < deadline) {{
    attempts += 1;
    const requestStart = Date.now();
    const response = http.get("http://127.0.0.1:8080/ping", {{ timeout: "{request_timeout_ms}ms" }});
    const responseComplete = Date.now();
    let body = null;
    try {{
      body = JSON.parse(response.body);
    }} catch (_) {{
      body = null;
    }}
    if (
      response.status === 200 &&
      body !== null &&
      Object.keys(body).length === 1 &&
      body.message === "pong"
    ) {{
      console.log(`HRW_FIRST_REQUEST_START_EPOCH_MS=${{requestStart}}`);
      console.log(`HRW_FIRST_SUCCESS_EPOCH_MS=${{responseComplete}}`);
      console.log(`HRW_FIRST_REQUEST_DURATION_MS=${{responseComplete - requestStart}}`);
      console.log(`HRW_ATTEMPTS=${{attempts}}`);
      while (true) {{
        sleep(1);
      }}
    }}
    sleep({poll_interval_ms / 1000});
  }}
  fail("target did not return the exact ping contract before the timeout");
}}
'''


def _target_started_at(pod: dict[str, Any]) -> datetime:
    statuses = pod.get("status", {}).get("containerStatuses", [])
    values = [
        status.get("state", {}).get("running", {}).get("startedAt")
        for status in statuses
        if status.get("name") == "target"
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        raise LifecycleEvidenceError("Target container running.startedAt is missing")
    try:
        value = datetime.fromisoformat(values[0].replace("Z", "+00:00"))
    except ValueError as error:
        raise LifecycleEvidenceError("Target container startedAt is invalid") from error
    if value.tzinfo is None:
        raise LifecycleEvidenceError("Target container startedAt has no timezone")
    return value


def _was_oom_killed(status: dict[str, Any]) -> bool:
    return any(
        status.get(state_name, {}).get("terminated", {}).get("reason")
        == "OOMKilled"
        for state_name in ("state", "lastState")
    )


def _resource_pair(resources: Mapping[str, str]) -> dict[str, dict[str, str]]:
    cpu = resources.get("cpu")
    memory = resources.get("memory")
    if not isinstance(cpu, str) or not isinstance(memory, str):
        raise ValueError("Lifecycle resources require cpu and memory")
    values = {"cpu": cpu, "memory": memory}
    return {"requests": values, "limits": dict(values)}


def _single_integer_marker(log: str, name: str) -> int:
    values = re.findall(
        rf'(?:^|[\s"]){re.escape(name)}=([0-9]+)(?=[\s"]|$)', log
    )
    if len(values) != 1:
        raise LifecycleEvidenceError(f"Expected exactly one {name} marker")
    return int(values[0])


def _single_number_marker(log: str, name: str) -> float:
    values = re.findall(
        rf'(?:^|[\s"]){re.escape(name)}=([0-9]+(?:\.[0-9]+)?)(?=[\s"]|$)',
        log,
    )
    if len(values) != 1:
        raise LifecycleEvidenceError(f"Expected exactly one {name} marker")
    return float(values[0])


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
