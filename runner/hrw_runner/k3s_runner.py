from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from statistics import mean
from typing import Any

from .config import RunConfig
from .evidence import (
    build_artifact_manifest,
    build_trial_summary,
    sha256_file,
    summarize_trials,
    validate_evidence_document,
    validate_run_set_evidence,
)
from .kubernetes import Kubectl, evaluate_preflight
from .kubernetes_image import build_and_export_image, build_and_push_image
from .kubernetes_render import render_scenario_documents
from .kubernetes_stats import normalize_stats_sample, validate_stats_series
from .manifest import build_resolved_manifest, read_git_provenance, validate_resolved_manifest
from .results import k6_runtime_metrics, read_json, write_json
from .runner import (
    RESULT_SCHEMA_VERSION,
    _metadata,
    _run_set_paths,
    _runtime,
    _trial_validity,
    _utc_timestamp,
)


def run_k3s_benchmark_set(config: RunConfig, root_dir: Path) -> Path:
    supported_scenarios = {
        "ping-api",
        "transactional-command-api",
        "io-aggregation-api",
    }
    if config.scenario not in supported_scenarios:
        raise ValueError(f"The k3s runner does not support {config.scenario}")
    paths = _run_set_paths(config, root_dir)
    paths.result_dir.mkdir(parents=True, exist_ok=False)
    source = read_git_provenance(root_dir)
    environment = config.environment_profile_config
    cluster = environment["cluster"]
    client = Kubectl(str(cluster["context"]), root_dir)
    preflight = evaluate_preflight(client, environment)
    write_json(paths.result_dir / "preflight.json", preflight)
    if preflight["status"] != "valid":
        raise RuntimeError("k3s preflight failed: " + "; ".join(preflight["reasons"]))

    distribution = os.environ.get("HRW_IMAGE_DISTRIBUTION", "push")
    prebuilt_image = os.environ.get("HRW_TARGET_IMAGE")
    prebuilt_archive = os.environ.get("HRW_TARGET_IMAGE_ARCHIVE")
    image_archive = paths.result_dir / "target-image.oci.tar"
    image_arguments = (
        config.app_dir,
        config.official_image_repository,
        str(source["git_commit"]),
    )
    if prebuilt_image:
        repository = config.official_image_repository
        prefix = f"{repository}@sha256:"
        digest = prebuilt_image.removeprefix(prefix)
        if not prebuilt_image.startswith(prefix) or re.fullmatch(
            r"[0-9a-f]{64}", digest
        ) is None:
            raise ValueError("HRW_TARGET_IMAGE must use the official immutable repository")
        if prebuilt_archive:
            image_archive = Path(prebuilt_archive).resolve()
            if not image_archive.is_file():
                raise ValueError("HRW_TARGET_IMAGE_ARCHIVE does not exist")
            distribution = "prebuilt-import"
        else:
            distribution = "prebuilt"
        image = {
            "image": prebuilt_image,
            "digest": f"sha256:{digest}",
            "platform": "linux/amd64",
            "distribution": distribution,
            "clean_build_ms": None,
            "image_build_ms": None,
        }
    elif distribution == "push":
        image = build_and_push_image(
            *image_arguments,
            str(config.runtime.get("java_version", "25")),
        )
    elif distribution == "import":
        image = build_and_export_image(
            *image_arguments,
            image_archive,
            str(config.runtime.get("java_version", "25")),
        )
    else:
        raise ValueError("HRW_IMAGE_DISTRIBUTION must be 'push' or 'import'")
    write_json(paths.result_dir / "build.json", image)
    execution_config = replace(config, image_tag=str(image["image"]))
    manifest = build_resolved_manifest(execution_config, paths.run_id, source)
    validate_resolved_manifest(manifest, root_dir)
    write_json(paths.result_dir / "resolved-manifest.json", manifest)
    manifest_digest = str(manifest["manifest_digest"])
    cohort = manifest["cohort"]
    assert isinstance(cohort, dict)
    cohort_fingerprint = str(cohort["fingerprint"])
    metadata = _metadata(
        execution_config,
        paths.run_id,
        manifest_digest,
        cohort_fingerprint,
    )
    metadata["environment"] = preflight["cluster"]
    write_json(paths.result_dir / "metadata.json", metadata)

    namespace = _namespace(paths.run_id, str(source["git_commit"]))
    trial_count = int(config.measurement_protocol_config["trials"])
    trial_documents: list[dict[str, Any]] = []
    trial_references = []
    started_at = _utc_timestamp()
    script = (root_dir / str(config.load["script"])).read_text()
    template = root_dir / "infra/k8s" / f"{config.scenario}.yaml"
    try:
        setup = _render(
            execution_config,
            environment,
            template,
            namespace,
            script,
            "k6-setup",
            str(config.load["test_duration"]),
        )
        client.apply([_kind(setup, "Namespace")])
        if distribution in {"import", "prebuilt-import"}:
            _import_image(client, namespace, image_archive)
            if distribution == "import":
                image_archive.unlink()
        dependency_start = time.perf_counter()
        dependencies = _component_documents(setup, "dependency")
        dependency_resources = [
            document for document in dependencies if document["kind"] != "Pod"
        ]
        dependency_pods = [
            document for document in dependencies if document["kind"] == "Pod"
        ]
        if dependency_resources:
            client.apply(dependency_resources)
        if dependency_pods:
            client.apply(dependency_pods)
            for pod in dependency_pods:
                client.command(
                    [
                        "wait",
                        "--for=condition=Ready",
                        f"pod/{pod['metadata']['name']}",
                        "-n",
                        namespace,
                        "--timeout=120s",
                    ]
                )
        dependency_ready_ms = round(
            (time.perf_counter() - dependency_start) * 1000
        )
        shared_resources = [
            document
            for document in setup
            if _component_name(document) in {"target", "load-generator"}
            and document["kind"] not in {"Pod", "Job"}
        ]
        client.apply(shared_resources)
        _prepull_target(
            client,
            _component_document(setup, "Pod", "target"),
            namespace,
        )

        for index in range(1, trial_count + 1):
            trial_id = f"trial-{index:02d}"
            trial_dir = paths.result_dir / "trials" / f"{index:02d}"
            try:
                trial = _run_trial(
                    execution_config,
                    environment,
                    client,
                    template,
                    script,
                    namespace,
                    paths.run_id,
                    trial_id,
                    index,
                    trial_dir,
                    manifest_digest,
                    cohort_fingerprint,
                    image,
                    preflight["cluster"],
                    dependency_ready_ms,
                )
            except Exception as error:
                _cleanup_trial_workloads(client, namespace)
                trial = _write_failed_trial(
                    execution_config,
                    paths.run_id,
                    trial_id,
                    index,
                    trial_dir,
                    manifest_digest,
                    cohort_fingerprint,
                    image,
                    preflight["cluster"],
                    error,
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
    finally:
        client.command(
            [
                "delete",
                "namespace",
                namespace,
                "--ignore-not-found=true",
                "--wait=true",
                "--timeout=120s",
            ]
        )

    postflight = _wait_for_quiet_postflight(client, environment)
    write_json(paths.result_dir / "postflight.json", postflight)
    if postflight["status"] != "valid":
        raise RuntimeError("k3s postflight failed: " + "; ".join(postflight["reasons"]))

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
        "platform_evidence": {
            name: {
                "path": f"{name}.json",
                "sha256": sha256_file(paths.result_dir / f"{name}.json"),
            }
            for name in ("preflight", "postflight", "build")
        },
    }
    validate_evidence_document(run_set, "run-set", root_dir)
    write_json(paths.result_dir / "run-set.json", run_set)
    validate_run_set_evidence(paths.result_dir, root_dir)
    return paths.result_dir


def _run_trial(
    config: RunConfig,
    environment: dict[str, Any],
    client: Kubectl,
    template: Path,
    script: str,
    namespace: str,
    run_set_id: str,
    trial_id: str,
    trial_index: int,
    trial_dir: Path,
    manifest_digest: str,
    cohort_fingerprint: str,
    build: dict[str, object],
    cluster_metadata: dict[str, object],
    dependency_ready_ms: int,
) -> dict[str, Any]:
    trial_dir.mkdir(parents=True, exist_ok=False)
    started_at = _utc_timestamp()
    startup_start = time.perf_counter()
    target_documents = _render(
        config,
        environment,
        template,
        namespace,
        script,
        f"k6-target-{trial_index:02d}",
        str(config.load["test_duration"]),
    )
    client.apply([_component_document(target_documents, "Pod", "target")])
    client.command(
        [
            "wait",
            "--for=condition=Ready",
            "pod/target",
            "-n",
            namespace,
            "--timeout=120s",
        ]
    )
    ready_ms = round((time.perf_counter() - startup_start) * 1000)
    startup = {
        "dependency_ready_ms": dependency_ready_ms,
        "ready_ms": ready_ms,
        "first_request_ms": None,
        "iterations": 1,
    }
    write_json(trial_dir / "startup.json", startup)

    warmup_name = f"k6-warmup-{trial_index:02d}"
    warmup = _render(
        config,
        environment,
        template,
        namespace,
        script,
        warmup_name,
        str(config.load["warmup_duration"]),
    )
    client.apply([_kind(warmup, "Job")])
    try:
        _wait_job(client, namespace, warmup_name, config.load["warmup_duration"])
    except Exception:
        _collect_job_log(
            client, namespace, warmup_name, trial_dir / "k6-warmup.log"
        )
        raise
    _collect_job(
        client,
        namespace,
        warmup_name,
        trial_dir / "k6-warmup-summary.json",
        trial_dir / "k6-warmup.log",
    )
    client.command(["delete", "job", warmup_name, "-n", namespace, "--wait=true"])
    _reset_scenario_state(client, namespace, config.scenario)

    measured_name = f"k6-measured-{trial_index:02d}"
    measured = _render(
        config,
        environment,
        template,
        namespace,
        script,
        measured_name,
        str(config.load["test_duration"]),
    )
    samples: list[dict[str, Any]] = []
    raw_snapshots: list[dict[str, Any]] = []
    stop = threading.Event()
    sample_start = time.perf_counter()
    sampler = threading.Thread(
        target=_sample_kubelet_stats,
        args=(client, str(environment["cluster"]["node_name"]), namespace, sample_start, samples, raw_snapshots, stop),
        daemon=True,
    )
    client.apply([_kind(measured, "Job")])
    sampler.start()
    try:
        _wait_job(client, namespace, measured_name, config.load["test_duration"])
    except Exception:
        _collect_job_log(client, namespace, measured_name, trial_dir / "k6.log")
        raise
    finally:
        stop.set()
        sampler.join(timeout=3)
    summary_path = trial_dir / "k6-summary.json"
    _collect_job(
        client,
        namespace,
        measured_name,
        summary_path,
        trial_dir / "k6.log",
    )
    k6_summary = read_json(summary_path)
    correctness = _scenario_correctness(
        client,
        namespace,
        config.scenario,
        k6_summary,
    )
    write_json(trial_dir / "correctness.json", correctness)
    write_json(trial_dir / "kubelet-stats.json", {"snapshots": raw_snapshots})
    target_pod = client.json(["get", "pod", "target", "-n", namespace])
    write_json(trial_dir / "target-pod.json", target_pod)
    dependency_pods, dependency_reasons = _collect_dependency_evidence(
        client, namespace, trial_dir
    )
    write_json(trial_dir / "dependency-pods.json", {"items": dependency_pods})
    (trial_dir / "target.log").write_text(
        client.command(["logs", "pod/target", "-n", namespace], capture=True)
    )
    client.command(["delete", "job", measured_name, "-n", namespace, "--wait=true"])
    client.command(["delete", "pod", "target", "-n", namespace, "--wait=true"])

    time_series = {
        "schema_version": "1.0",
        "trial_id": trial_id,
        "sample_interval_ms": int(environment["validity"]["stats_sample_interval_seconds"]) * 1000,
        "samples": samples,
    }
    validate_evidence_document(time_series, "time-series", config.root_dir)
    time_series_path = trial_dir / "time-series.json"
    write_json(time_series_path, time_series)
    stats_validity = validate_stats_series(
        samples,
        _duration_seconds(str(config.load["test_duration"])),
        environment["validity"],
        dependency_expected=bool(config.scenario_config["dependencies"]),
    )
    write_json(trial_dir / "in-run-validity.json", stats_validity)

    application_status, application_reasons = _trial_validity(k6_summary)
    if correctness["status"] != "valid":
        application_status = "invalid"
        application_reasons.extend(correctness["reasons"])
    infrastructure_reasons = list(stats_validity["reasons"])
    if "rate" in config.load:
        dropped_iterations = _optional_k6_counter(k6_summary, "dropped_iterations")
        if dropped_iterations > 0:
            infrastructure_reasons.append(
                f"k6 dropped {dropped_iterations} scheduled iterations"
            )
    infrastructure_reasons.extend(_pod_failure_reasons(target_pod, config.image_tag))
    infrastructure_reasons.extend(dependency_reasons)
    if infrastructure_reasons:
        status = "invalid"
        invalidity_class = "infrastructure"
        invalid_reasons = infrastructure_reasons
    elif application_status != "valid":
        status = "invalid"
        invalidity_class = "application"
        invalid_reasons = application_reasons
    else:
        status = "valid"
        invalidity_class = None
        invalid_reasons = []

    runtime_metrics = k6_runtime_metrics(k6_summary)
    runtime_metrics.update(_resource_summary(samples))
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": trial_id,
        "run_set_id": run_set_id,
        "trial_index": trial_index,
        "manifest_digest": manifest_digest,
        "cohort_fingerprint": cohort_fingerprint,
        "project": "hello-realworld-bench",
        "scenario": config.scenario,
        "implementation": config.implementation,
        "variant": config.variant,
        "runtime": _runtime(config),
        "environment": cluster_metadata,
        "build": build,
        "startup": startup,
        "runtime_metrics": runtime_metrics,
    }
    write_json(trial_dir / "result.json", result)
    metadata = _metadata(config, trial_id, manifest_digest, cohort_fingerprint)
    metadata["run_set_id"] = run_set_id
    metadata["trial_index"] = trial_index
    metadata["environment"] = cluster_metadata
    write_json(trial_dir / "metadata.json", metadata)

    artifact_manifest = build_artifact_manifest(trial_id, trial_dir)
    validate_evidence_document(artifact_manifest, "artifact-manifest", config.root_dir)
    artifact_path = trial_dir / "artifact-manifest.json"
    write_json(artifact_path, artifact_manifest)
    trial_document: dict[str, Any] = {
        "schema_version": "1.0",
        "trial_id": trial_id,
        "run_id": run_set_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _utc_timestamp(),
        "manifest_digest": manifest_digest,
        "cohort_fingerprint": cohort_fingerprint,
        "summary": build_trial_summary(result, "kubelet-stats.json"),
        "time_series": {
            "path": "time-series.json",
            "sha256": sha256_file(time_series_path),
        },
        "artifact_manifest": {
            "path": "artifact-manifest.json",
            "sha256": sha256_file(artifact_path),
        },
    }
    if invalid_reasons:
        trial_document["invalid_reasons"] = invalid_reasons
        trial_document["invalidity_class"] = invalidity_class
    validate_evidence_document(trial_document, "trial", config.root_dir)
    write_json(trial_dir / "trial.json", trial_document)
    return {**trial_document, "result": result}


def _render(
    config: RunConfig,
    environment: dict[str, Any],
    template: Path,
    namespace: str,
    script: str,
    job_name: str,
    duration: str,
) -> list[dict[str, Any]]:
    summary_handler = """

export function handleSummary(data) {
  return { stdout: `HRW_SUMMARY_JSON=${JSON.stringify(data)}\\n` };
}
"""
    open_model = "rate" in config.load
    warmup = job_name.startswith("k6-warmup")
    executor = str(config.load.get("executor", "constant-vus"))
    if open_model and warmup:
        executor = "constant-arrival-rate"
    return render_scenario_documents(
        template,
        namespace=namespace,
        run_set_id=namespace,
        target_image=config.image_tag,
        k6_image=str(environment["images"]["k6"]),
        java_tool_options="-XX:MaxRAMPercentage=75",
        duration=duration,
        vus=int(config.load["vus"]),
        job_name=job_name,
        script=script + summary_handler,
        scenario_id=config.scenario,
        executor=executor,
        rate=int(
            config.load.get("warmup_rate" if warmup else "rate", 1)
        ),
        stages=json.dumps(config.load.get("stages", []), separators=(",", ":")),
        pre_allocated_vus=int(config.load.get("pre_allocated_vus", 1)),
        max_vus=int(config.load.get("max_vus", 1)),
        target_environment=config.target_environment,
    )


def _kind(documents: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(document for document in documents if document["kind"] == kind)


def _component_name(document: dict[str, Any]) -> str | None:
    return document.get("metadata", {}).get("labels", {}).get(
        "app.kubernetes.io/component"
    )


def _component_documents(
    documents: list[dict[str, Any]], component: str
) -> list[dict[str, Any]]:
    return [
        document for document in documents if _component_name(document) == component
    ]


def _component_document(
    documents: list[dict[str, Any]], kind: str, component: str
) -> dict[str, Any]:
    return next(
        document
        for document in documents
        if document["kind"] == kind and _component_name(document) == component
    )


def _prepull_target(client: Kubectl, target: dict[str, Any], namespace: str) -> None:
    pod = copy.deepcopy(target)
    pod["metadata"]["name"] = "target-image-prepull"
    pod["spec"]["restartPolicy"] = "Never"
    container = pod["spec"]["containers"][0]
    container["command"] = ["/bin/sh", "-c", "true"]
    container.pop("readinessProbe", None)
    client.apply([pod])
    client.command(
        [
            "wait",
            "--for=jsonpath={.status.phase}=Succeeded",
            "pod/target-image-prepull",
            "-n",
            namespace,
            "--timeout=120s",
        ]
    )
    client.command(
        ["delete", "pod", "target-image-prepull", "-n", namespace, "--wait=true"]
    )


def _import_image(client: Kubectl, namespace: str, archive: Path) -> None:
    loader = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "image-loader",
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/part-of": "hello-realworld-bench",
                "app.kubernetes.io/component": "image-loader",
            },
        },
        "spec": {
            "automountServiceAccountToken": False,
            "nodeSelector": {"kubernetes.io/hostname": "homlab"},
            "containers": [
                {
                    "name": "loader",
                    "image": "ubuntu@sha256:52df9b1ee71626e0088f7d400d5c6b5f7bb916f8f0c82b474289a4ece6cf3faf",
                    "command": ["sleep", "3600"],
                    "securityContext": {"privileged": True},
                    "resources": {
                        "requests": {"cpu": "1", "memory": "1Gi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                    "volumeMounts": [
                        {"name": "host-root", "mountPath": "/host"},
                        {"name": "image-drop", "mountPath": "/images"},
                    ],
                }
            ],
            "volumes": [
                {"name": "host-root", "hostPath": {"path": "/"}},
                {
                    "name": "image-drop",
                    "hostPath": {"path": "/var/lib/rancher/k3s/agent/images"},
                },
            ],
        },
    }
    client.apply([loader])
    client.command(
        [
            "wait",
            "--for=condition=Ready",
            "pod/image-loader",
            "-n",
            namespace,
            "--timeout=120s",
        ]
    )
    remote_archive = "/images/hello-realworld-target.oci.tar"
    client.command(
        ["cp", str(archive), f"{namespace}/image-loader:{remote_archive}", "-c", "loader"]
    )
    client.command(
        [
            "exec",
            "pod/image-loader",
            "-n",
            namespace,
            "--",
            "chroot",
            "/host",
            "/usr/local/bin/k3s",
            "ctr",
            "images",
            "import",
            "/var/lib/rancher/k3s/agent/images/hello-realworld-target.oci.tar",
        ]
    )
    client.command(
        [
            "exec",
            "pod/image-loader",
            "-n",
            namespace,
            "--",
            "rm",
            "-f",
            remote_archive,
        ]
    )
    client.command(["delete", "pod", "image-loader", "-n", namespace, "--wait=true"])


def _wait_job(client: Kubectl, namespace: str, name: str, duration: object) -> None:
    timeout = _duration_seconds(str(duration)) + 120
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.json(["get", "job", name, "-n", namespace])
        status = job.get("status", {})
        if int(status.get("succeeded", 0)) >= 1:
            return
        if int(status.get("failed", 0)) >= 1:
            conditions = status.get("conditions", [])
            message = next(
                (
                    condition.get("message") or condition.get("reason")
                    for condition in conditions
                    if condition.get("type") == "Failed"
                ),
                "job pod failed",
            )
            raise RuntimeError(f"Kubernetes job {name} failed: {message}")
        time.sleep(1)
    raise TimeoutError(f"Kubernetes job {name} did not finish within {timeout}s")


def _collect_job(
    client: Kubectl,
    namespace: str,
    job_name: str,
    summary_path: Path,
    log_path: Path,
) -> None:
    pods = client.json(
        ["get", "pods", "-n", namespace, "-l", f"job-name={job_name}"]
    )["items"]
    if len(pods) != 1:
        raise RuntimeError(f"Expected one pod for job {job_name}, got {len(pods)}")
    pod_name = pods[0]["metadata"]["name"]
    log_path.write_text(
        client.command(["logs", pod_name, "-n", namespace], capture=True)
    )
    summary = _summary_from_k6_log(log_path.read_text())
    write_json(summary_path, summary)


def _collect_job_log(
    client: Kubectl, namespace: str, job_name: str, log_path: Path
) -> None:
    pods = client.json(
        ["get", "pods", "-n", namespace, "-l", f"job-name={job_name}"]
    ).get("items", [])
    if not pods:
        log_path.write_text("No pod was created for the failed job.\n")
        return
    pod_name = pods[0]["metadata"]["name"]
    log_path.write_text(
        client.command(["logs", pod_name, "-n", namespace], capture=True)
    )


def _summary_from_k6_log(log: str) -> dict[str, Any]:
    marker = "HRW_SUMMARY_JSON="
    lines = [line for line in log.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise RuntimeError(f"Expected one k6 summary marker, got {len(lines)}")
    value = json.loads(lines[0].removeprefix(marker))
    if not isinstance(value, dict):
        raise RuntimeError("k6 summary marker must contain a JSON object")
    return value


def _sample_kubelet_stats(
    client: Kubectl,
    node_name: str,
    namespace: str,
    started: float,
    samples: list[dict[str, Any]],
    raw_snapshots: list[dict[str, Any]],
    stop: threading.Event,
) -> None:
    last_source_time = None
    while not stop.is_set():
        snapshot = client.json(
            ["get", "--raw", f"/api/v1/nodes/{node_name}/proxy/stats/summary"]
        )
        raw_snapshots.append(snapshot)
        source_time = snapshot["node"]["cpu"]["time"]
        if source_time != last_source_time:
            samples.append(
                normalize_stats_sample(
                    snapshot,
                    namespace,
                    round((time.perf_counter() - started) * 1000),
                )
            )
            last_source_time = source_time
        stop.wait(1)


def _resource_summary(samples: list[dict[str, Any]]) -> dict[str, object]:
    cpu = [float(sample["target_cpu_percent"]) for sample in samples]
    memory = [int(sample["target_memory_bytes"]) for sample in samples]
    return {
        "cpu_percent_avg": round(mean(cpu), 4) if cpu else None,
        "cpu_percent_max": max(cpu) if cpu else None,
        "memory_usage_max_bytes": max(memory) if memory else None,
    }


def _reset_scenario_state(
    client: Kubectl, namespace: str, scenario: str
) -> None:
    if scenario != "transactional-command-api":
        return
    client.command(
        [
            "exec",
            "pod/postgres",
            "-n",
            namespace,
            "--",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "hrw",
            "-d",
            "hrw",
            "-c",
            "truncate table order_items, outbox_events, orders;",
        ],
        capture=True,
    )


def _scenario_correctness(
    client: Kubectl,
    namespace: str,
    scenario: str,
    k6_summary: dict[str, Any],
) -> dict[str, Any]:
    if scenario != "transactional-command-api":
        return {
            "status": "valid",
            "oracle": "k6-semantic-response-checks",
            "reasons": [],
        }
    expected = _k6_counter(k6_summary, "iterations")
    output = client.command(
        [
            "exec",
            "pod/postgres",
            "-n",
            namespace,
            "--",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "hrw",
            "-d",
            "hrw",
            "-At",
            "-c",
            "select (select count(*) from orders) || ',' || "
            "(select count(*) from order_items) || ',' || "
            "(select count(*) from outbox_events);",
        ],
        capture=True,
    ).strip()
    try:
        orders, order_items, outbox_events = (int(value) for value in output.split(","))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"Invalid PostgreSQL correctness output: {output}") from error
    observed = {
        "orders": orders,
        "order_items": order_items,
        "outbox_events": outbox_events,
    }
    reasons = [
        f"{name} row count {value} does not match {expected} measured iterations"
        for name, value in observed.items()
        if value != expected
    ]
    return {
        "status": "valid" if not reasons else "invalid",
        "oracle": "transactional-row-counts",
        "expected_iterations": expected,
        "observed": observed,
        "reasons": reasons,
    }


def _k6_counter(summary: dict[str, Any], name: str) -> int:
    metric = summary.get("metrics", {}).get(name, {})
    values = metric.get("values", {}) if isinstance(metric, dict) else {}
    count = values.get("count") if isinstance(values, dict) else None
    if not isinstance(count, (int, float)) or count < 0:
        raise RuntimeError(f"k6 summary is missing {name} count")
    return int(count)


def _optional_k6_counter(summary: dict[str, Any], name: str) -> int:
    metric = summary.get("metrics", {}).get(name)
    if metric is None:
        return 0
    return _k6_counter(summary, name)


def _pod_failure_reasons(
    pod: dict[str, Any], expected_image: str
) -> list[str]:
    reasons = []
    for status in pod.get("status", {}).get("containerStatuses", []):
        image_id = str(status.get("imageID", "")).removeprefix("docker-pullable://")
        if image_id != expected_image:
            reasons.append(
                f"target imageID {image_id or 'missing'} does not match {expected_image}"
            )
        if int(status.get("restartCount", 0)) > 0:
            reasons.append(f"target restarted {status['restartCount']} time(s)")
        if _container_was_oom_killed(status):
            reasons.append("target was OOMKilled")
    return reasons


def _collect_dependency_evidence(
    client: Kubectl, namespace: str, trial_dir: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    response = client.json(
        [
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            "app.kubernetes.io/component=dependency",
        ]
    )
    pods = response.get("items", [])
    reasons = []
    for pod in pods:
        name = str(pod["metadata"]["name"])
        (trial_dir / f"{name}.log").write_text(
            client.command(["logs", f"pod/{name}", "-n", namespace], capture=True)
        )
        for status in pod.get("status", {}).get("containerStatuses", []):
            if int(status.get("restartCount", 0)) > 0:
                reasons.append(f"dependency {name} restarted")
            if _container_was_oom_killed(status):
                reasons.append(f"dependency {name} was OOMKilled")
    return pods, reasons


def _container_was_oom_killed(status: dict[str, Any]) -> bool:
    return any(
        status.get(state_name, {}).get("terminated", {}).get("reason")
        == "OOMKilled"
        for state_name in ("state", "lastState")
    )


def _duration_seconds(value: str) -> int:
    multipliers = {"s": 1, "m": 60, "h": 3600}
    if len(value) < 2 or value[-1] not in multipliers:
        raise ValueError(f"Unsupported duration: {value}")
    return int(value[:-1]) * multipliers[value[-1]]


def _cleanup_trial_workloads(client: Kubectl, namespace: str) -> None:
    for arguments in (
        [
            "delete",
            "jobs",
            "-n",
            namespace,
            "-l",
            "app.kubernetes.io/component=load-generator",
            "--ignore-not-found=true",
            "--wait=true",
        ],
        [
            "delete",
            "pod/target",
            "-n",
            namespace,
            "--ignore-not-found=true",
            "--wait=true",
        ],
    ):
        try:
            client.command(arguments)
        except Exception:
            pass


def _write_failed_trial(
    config: RunConfig,
    run_set_id: str,
    trial_id: str,
    trial_index: int,
    trial_dir: Path,
    manifest_digest: str,
    cohort_fingerprint: str,
    build: dict[str, object],
    cluster_metadata: dict[str, object],
    error: Exception,
) -> dict[str, Any]:
    trial_dir.mkdir(parents=True, exist_ok=True)
    reason = f"{type(error).__name__}: {error}"
    write_json(trial_dir / "error.json", {"classification": "infrastructure", "reason": reason})
    time_series = {
        "schema_version": "1.0",
        "trial_id": trial_id,
        "sample_interval_ms": 10_000,
        "samples": [],
    }
    time_series_path = trial_dir / "time-series.json"
    validate_evidence_document(time_series, "time-series", config.root_dir)
    write_json(time_series_path, time_series)
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": trial_id,
        "run_set_id": run_set_id,
        "trial_index": trial_index,
        "manifest_digest": manifest_digest,
        "cohort_fingerprint": cohort_fingerprint,
        "project": "hello-realworld-bench",
        "scenario": config.scenario,
        "implementation": config.implementation,
        "variant": config.variant,
        "runtime": _runtime(config),
        "environment": cluster_metadata,
        "build": build,
        "startup": {},
        "runtime_metrics": {},
    }
    write_json(trial_dir / "result.json", result)
    artifact_manifest = build_artifact_manifest(trial_id, trial_dir)
    validate_evidence_document(artifact_manifest, "artifact-manifest", config.root_dir)
    artifact_path = trial_dir / "artifact-manifest.json"
    write_json(artifact_path, artifact_manifest)
    trial_document = {
        "schema_version": "1.0",
        "trial_id": trial_id,
        "run_id": run_set_id,
        "status": "failed",
        "invalidity_class": "infrastructure",
        "invalid_reasons": [reason],
        "started_at": _utc_timestamp(),
        "finished_at": _utc_timestamp(),
        "manifest_digest": manifest_digest,
        "cohort_fingerprint": cohort_fingerprint,
        "summary": [],
        "time_series": {
            "path": "time-series.json",
            "sha256": sha256_file(time_series_path),
        },
        "artifact_manifest": {
            "path": "artifact-manifest.json",
            "sha256": sha256_file(artifact_path),
        },
    }
    validate_evidence_document(trial_document, "trial", config.root_dir)
    write_json(trial_dir / "trial.json", trial_document)
    return {**trial_document, "result": result}


def _namespace(run_set_id: str, git_commit: str) -> str:
    timestamp = run_set_id.split("_", 1)[0].replace("-", "").lower()
    return f"hrw-{timestamp}-{git_commit[:7]}"


def _wait_for_quiet_postflight(
    client: Kubectl,
    environment: dict[str, Any],
    timeout_seconds: int = 60,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = evaluate_preflight(client, environment)
        if result["status"] == "valid":
            return result
        retryable = all(
            reason.startswith("background CPU")
            or reason.startswith("background memory")
            for reason in result["reasons"]
        )
        if not retryable or time.monotonic() >= deadline:
            return result
        time.sleep(5)
