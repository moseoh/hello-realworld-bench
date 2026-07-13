from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from dataclasses import replace
from datetime import datetime
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


_TIMELINE_SCENARIOS = {
    "transactional-command-api",
    "io-aggregation-api",
    "read-heavy-query-api",
}
_TIMELINE_BUCKET_SECONDS = 10


def run_k3s_benchmark_set(config: RunConfig, root_dir: Path) -> Path:
    supported_scenarios = {
        "ping-api",
        "transactional-command-api",
        "io-aggregation-api",
        "read-heavy-query-api",
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
        if config.scenario == "read-heavy-query-api":
            dataset_preflight = _scenario_correctness(
                client,
                namespace,
                config.scenario,
                {},
                config.scenario_config,
            )
            write_json(paths.result_dir / "dataset-preflight.json", dataset_preflight)
            if dataset_preflight["status"] != "valid":
                raise RuntimeError(
                    "read-heavy dataset preflight failed: "
                    + "; ".join(dataset_preflight["reasons"])
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
    dataset_preflight_path = paths.result_dir / "dataset-preflight.json"
    if dataset_preflight_path.is_file():
        run_set["platform_evidence"]["dataset_preflight"] = {
            "path": "dataset-preflight.json",
            "sha256": sha256_file(dataset_preflight_path),
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
        config.scenario_config,
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

    timeline_required = config.scenario in _TIMELINE_SCENARIOS
    sample_interval_seconds = (
        _TIMELINE_BUCKET_SECONDS
        if timeline_required
        else int(environment["validity"]["stats_sample_interval_seconds"])
    )
    time_series = {
        "schema_version": "1.0",
        "trial_id": trial_id,
        "sample_interval_ms": sample_interval_seconds * 1000,
        "samples": _build_runtime_timeline(
            samples,
            k6_summary,
            _duration_seconds(str(config.load["test_duration"])),
            config.load,
            bucket_seconds=_TIMELINE_BUCKET_SECONDS,
            required=timeline_required,
        ),
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
    postgres_init_sql = None
    if config.scenario == "read-heavy-query-api":
        postgres_init_sql = _read_dataset_init_sql(config)
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
        postgres_init_sql=postgres_init_sql,
        target_environment=config.target_environment,
    )


def _read_dataset_init_sql(config: RunConfig) -> str:
    dataset = config.scenario_config.get("dataset", {})
    asset = dataset.get("asset") if isinstance(dataset, dict) else None
    if not isinstance(asset, str) or not asset:
        raise ValueError("read-heavy-query-api requires dataset.asset")
    root = Path(config.root_dir).resolve()
    scenario_dir = Path(config.scenario_dir).resolve()
    path = (root / asset).resolve()
    if not path.is_relative_to(scenario_dir) or not path.is_file():
        raise ValueError("read-heavy dataset.asset must be a scenario file")
    return path.read_text()


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


def _build_runtime_timeline(
    resource_samples: list[dict[str, Any]],
    k6_summary: dict[str, Any],
    measured_seconds: int,
    load: dict[str, Any],
    bucket_seconds: int = _TIMELINE_BUCKET_SECONDS,
    *,
    required: bool = False,
) -> list[dict[str, Any]]:
    metrics = k6_summary.get("metrics", {})
    if not isinstance(metrics, dict) or not any(
        str(name).startswith("hrw_timeline_requests{") for name in metrics
    ):
        if required:
            raise RuntimeError("Core scenario is missing k6 timeline metrics")
        return resource_samples
    origin_ms = _timeline_origin_ms(metrics)

    samples = []
    bucket_count = max(1, (measured_seconds + bucket_seconds - 1) // bucket_seconds)
    for bucket in range(bucket_count):
        start_seconds = bucket * bucket_seconds
        end_seconds = min(measured_seconds, (bucket + 1) * bucket_seconds)
        window_seconds = end_seconds - start_seconds
        elapsed_ms = end_seconds * 1000
        resource = _nearest_resource_sample(resource_samples, elapsed_ms, origin_ms)
        request_values = _timeline_metric_values(
            metrics, "hrw_timeline_requests", bucket
        )
        failure_values = _timeline_metric_values(
            metrics, "hrw_timeline_failures", bucket
        )
        duration_values = _timeline_metric_values(
            metrics, "hrw_timeline_duration", bucket
        )
        request_count = int(request_values.get("count", 0))
        failure_count = int(failure_values.get("count", 0))
        midpoint_seconds = start_seconds + window_seconds / 2
        samples.append(
            {
                **resource,
                "elapsed_ms": elapsed_ms,
                "requested_rps": _requested_rps(load, midpoint_seconds),
                "achieved_rps": round(request_count / window_seconds, 4),
                "request_count": request_count,
                "failure_count": failure_count,
                "error_rate": (
                    round(failure_count / request_count, 8)
                    if request_count > 0
                    else None
                ),
                "p50_ms": _number_or_none(duration_values.get("med")),
                "p95_ms": _number_or_none(duration_values.get("p(95)")),
                "p99_ms": _number_or_none(duration_values.get("p(99)")),
            }
        )
    return samples


def _nearest_resource_sample(
    samples: list[dict[str, Any]], elapsed_ms: int, origin_ms: float
) -> dict[str, Any]:
    if samples:
        return dict(
            min(
                samples,
                key=lambda sample: abs(
                    _resource_elapsed_ms(sample, origin_ms) - elapsed_ms
                ),
            )
        )
    return {
        "target_cpu_percent": None,
        "target_memory_bytes": None,
        "target_memory_percent": None,
    }


def _timeline_origin_ms(metrics: dict[str, Any]) -> float:
    values = _timeline_metric_values(metrics, "hrw_timeline_origin_ms", None)
    origin = values.get("value")
    if not isinstance(origin, (int, float)) or origin <= 0:
        raise RuntimeError("k6 timeline is missing its scenario start timestamp")
    return float(origin)


def _resource_elapsed_ms(sample: dict[str, Any], origin_ms: float) -> int:
    source_time = sample.get("source_time")
    if not isinstance(source_time, str):
        raise RuntimeError("Kubernetes resource sample is missing source_time")
    try:
        timestamp = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("Kubernetes resource sample has invalid source_time") from error
    if timestamp.tzinfo is None:
        raise RuntimeError("Kubernetes resource sample source_time has no timezone")
    return round(timestamp.timestamp() * 1000 - origin_ms)


def _timeline_metric_values(
    metrics: dict[str, Any], name: str, bucket: int | None
) -> dict[str, Any]:
    key = name if bucket is None else f"{name}{{bucket:{bucket}}}"
    metric = metrics.get(key, {})
    if not isinstance(metric, dict):
        return {}
    values = metric.get("values", metric)
    return values if isinstance(values, dict) else {}


def _requested_rps(load: dict[str, Any], elapsed_seconds: float) -> float | None:
    executor = load.get("executor")
    rate = load.get("rate")
    if executor == "constant-arrival-rate" and isinstance(rate, (int, float)):
        return float(rate)
    if executor != "ramping-arrival-rate" or not isinstance(rate, (int, float)):
        return None

    current = float(rate)
    phase_start = 0.0
    stages = load.get("stages", [])
    if not isinstance(stages, list):
        return None
    for stage in stages:
        if not isinstance(stage, dict):
            return None
        target = stage.get("target")
        duration = stage.get("duration")
        if not isinstance(target, (int, float)) or not isinstance(duration, str):
            return None
        seconds = _duration_seconds(duration)
        if seconds == 0:
            current = float(target)
            continue
        if elapsed_seconds <= phase_start + seconds:
            progress = max(0.0, elapsed_seconds - phase_start) / seconds
            return round(current + (float(target) - current) * progress, 4)
        phase_start += seconds
        current = float(target)
    return current


def _number_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


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
    scenario_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if scenario == "read-heavy-query-api":
        if scenario_config is None:
            raise ValueError("read-heavy correctness requires scenario configuration")
        return _read_heavy_correctness(client, namespace, scenario_config)
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


def _read_heavy_correctness(
    client: Kubectl,
    namespace: str,
    scenario_config: dict[str, Any],
) -> dict[str, Any]:
    dataset = scenario_config.get("dataset")
    query_contract = scenario_config.get("query_contract")
    if not isinstance(dataset, dict) or not isinstance(query_contract, dict):
        raise ValueError("read-heavy dataset and query contract must be objects")
    fingerprint = dataset.get("fingerprint")
    index_name = query_contract.get("index")
    if not isinstance(fingerprint, dict) or not isinstance(index_name, str):
        raise ValueError("read-heavy fingerprint and index are required")
    if re.fullmatch(r"[a-z_][a-z0-9_]*", index_name) is None:
        raise ValueError("read-heavy index name is invalid")

    expected = {
        "row_count": int(dataset["row_count"]),
        "id_sum": int(fingerprint["id_sum"]),
        "price_cents_sum": int(fingerprint["price_cents_sum"]),
        "rating_basis_points_sum": int(fingerprint["rating_basis_points_sum"]),
        "active_count": int(fingerprint["active_count"]),
        "index_count": 1,
    }
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
            "select count(*) || ',' || coalesce(sum(id), 0) || ',' || "
            "coalesce(sum(price_cents), 0) || ',' || "
            "coalesce(sum(rating_basis_points), 0) || ',' || "
            "count(*) filter (where active) || ',' || "
            "(select count(*) from pg_indexes where schemaname = 'public' "
            "and tablename = 'catalog_products' "
            f"and indexname = '{index_name}') from catalog_products;",
        ],
        capture=True,
    ).strip()
    names = tuple(expected)
    try:
        observed = dict(zip(names, (int(value) for value in output.split(",")), strict=True))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"Invalid PostgreSQL fingerprint output: {output}") from error
    reasons = [
        f"{name} {observed[name]} does not match expected {value}"
        for name, value in expected.items()
        if observed[name] != value
    ]
    return {
        "status": "valid" if not reasons else "invalid",
        "oracle": "read-heavy-dataset-fingerprint",
        "expected": expected,
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
