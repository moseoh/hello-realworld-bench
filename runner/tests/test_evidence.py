import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hrw_runner.evidence import (
    build_artifact_manifest,
    build_compact_time_series,
    build_trial_summary,
    sha256_file,
    summarize_trials,
    validate_evidence_document,
    validate_lifecycle_publication_evidence,
    validate_run_set_evidence,
)
from hrw_runner.kubernetes_lifecycle import (
    build_lifecycle_measurement,
    build_prepull_evidence,
    evaluate_lifecycle_boundaries,
)
from hrw_runner.kubernetes_stats import normalize_stats_sample


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class CompactTimeSeriesTest(unittest.TestCase):
    def test_normalizes_timestamped_docker_samples(self):
        document = build_compact_time_series(
            "trial-01",
            1.0,
            [
                {
                    "elapsed_ms": 0,
                    "CPUPerc": "125.5%",
                    "MemUsage": "128MiB / 1GiB",
                    "MemPerc": "12.5%",
                },
                {
                    "elapsed_ms": 1004,
                    "CPUPerc": "80.0%",
                    "MemUsage": "130.5MiB / 1GiB",
                    "MemPerc": "12.74%",
                },
            ],
        )

        self.assertEqual(document["schema_version"], "1.0")
        self.assertEqual(document["trial_id"], "trial-01")
        self.assertEqual(document["sample_interval_ms"], 1000)
        self.assertEqual(
            document["samples"][1],
            {
                "elapsed_ms": 1004,
                "target_cpu_percent": 80.0,
                "target_memory_bytes": 136839168,
                "target_memory_percent": 12.74,
            },
        )

    def test_rejects_invalid_or_partial_runtime_timeline_metrics(self):
        sample = {
            "elapsed_ms": 10_000,
            "target_cpu_percent": 10.0,
            "target_memory_bytes": 100,
            "target_memory_percent": 1.0,
            "requested_rps": 100.0,
            "achieved_rps": 99.0,
            "request_count": 990,
            "failure_count": 1,
            "error_rate": 0.01,
            "p50_ms": 1.0,
            "p95_ms": 2.0,
            "p99_ms": 3.0,
        }

        for field, value in (("achieved_rps", -1), ("p95_ms", -1), ("error_rate", 1.1)):
            with self.subTest(field=field):
                invalid = dict(sample)
                invalid[field] = value
                with self.assertRaises(ValueError):
                    validate_evidence_document(
                        {
                            "schema_version": "1.0",
                            "trial_id": "trial-01",
                            "sample_interval_ms": 10_000,
                            "samples": [invalid],
                        },
                        "time-series",
                        PROJECT_ROOT,
                    )

        partial = dict(sample)
        partial.pop("p99_ms")
        with self.assertRaises(ValueError):
            validate_evidence_document(
                {
                    "schema_version": "1.0",
                    "trial_id": "trial-01",
                    "sample_interval_ms": 10_000,
                    "samples": [partial],
                },
                "time-series",
                PROJECT_ROOT,
            )


class ArtifactManifestTest(unittest.TestCase):
    def test_records_relative_paths_sizes_and_sha256(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "result.json").write_text('{"ok": true}\n')
            (root / "nested").mkdir()
            (root / "nested/raw.log").write_text("evidence\n")

            document = build_artifact_manifest("trial-01", root)

        artifacts = {entry["path"]: entry for entry in document["artifacts"]}
        self.assertEqual(set(artifacts), {"nested/raw.log", "result.json"})
        self.assertEqual(
            artifacts["result.json"]["sha256"],
            hashlib.sha256(b'{"ok": true}\n').hexdigest(),
        )
        self.assertEqual(artifacts["nested/raw.log"]["size_bytes"], 9)


class RunSetSummaryTest(unittest.TestCase):
    def test_summarizes_valid_trials_without_selecting_a_favorable_trial(self):
        trials = [
            {
                "trial_id": "trial-01",
                "status": "valid",
                "result": {
                    "runtime_metrics": {"rps": 100.0, "p95_ms": 10.0},
                    "startup": {"ready_ms": 1000},
                },
            },
            {
                "trial_id": "trial-02",
                "status": "valid",
                "result": {
                    "runtime_metrics": {"rps": 120.0, "p95_ms": 12.0},
                    "startup": {"ready_ms": 1200},
                },
            },
            {
                "trial_id": "trial-03",
                "status": "valid",
                "result": {
                    "runtime_metrics": {"rps": 80.0, "p95_ms": 8.0},
                    "startup": {"ready_ms": 800},
                },
            },
        ]

        summary = summarize_trials(trials)

        self.assertEqual(summary["trial_count"], 3)
        self.assertEqual(summary["valid_trial_count"], 3)
        self.assertEqual(summary["runtime_metrics"]["rps"]["median"], 100.0)
        self.assertEqual(summary["runtime_metrics"]["rps"]["min"], 80.0)
        self.assertEqual(summary["runtime_metrics"]["rps"]["max"], 120.0)
        self.assertEqual(summary["runtime_metrics"]["p95_ms"]["median"], 10.0)
        self.assertEqual(summary["startup_metrics"]["ready_ms"]["median"], 1000.0)

    def test_summarizes_explicit_lifecycle_metric_across_all_valid_trials(self):
        trials = [
            {
                "trial_id": f"trial-{index:02d}",
                "status": "valid",
                "result": {
                    "runtime_metrics": {},
                    "startup": {
                        "entrypoint_pre_exec_to_first_valid_response_ms": value,
                    },
                },
            }
            for index, value in enumerate((900, 1100, 1000), start=1)
        ]

        metric = summarize_trials(trials)["startup_metrics"][
            "entrypoint_pre_exec_to_first_valid_response_ms"
        ]

        self.assertEqual(metric["min"], 900.0)
        self.assertEqual(metric["median"], 1000.0)
        self.assertEqual(metric["max"], 1100.0)
        self.assertEqual(len(metric["trials"]), 3)


class EvidenceBundleValidationTest(unittest.TestCase):
    def test_recomputes_lifecycle_startup_from_raw_logs_before_publication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_set_dir = Path(temp_dir)
            trial_dir = run_set_dir / "trials/01"
            trial_dir.mkdir(parents=True)
            target_image = "ghcr.io/example/target@sha256:" + "a" * 64
            observer_image = "grafana/k6@sha256:" + "b" * 64
            pod = {
                "metadata": {"namespace": "hrw-test"},
                "status": {
                    "containerStatuses": [
                        {
                            "name": "target",
                            "imageID": target_image,
                            "restartCount": 0,
                            "state": {
                                "running": {"startedAt": "1970-01-01T00:00:01Z"}
                            },
                        }
                    ],
                    "initContainerStatuses": [
                        {
                            "name": "observer",
                            "imageID": observer_image,
                            "restartCount": 0,
                            "state": {"running": {}},
                        }
                    ],
                }
            }
            target_log = "HRW_ENTRYPOINT_PRE_EXEC_EPOCH_MS=1000\n"
            observer_log = (
                "HRW_OBSERVER_READY_EPOCH_MS=900\n"
                "HRW_FIRST_REQUEST_START_EPOCH_MS=1200\n"
                "HRW_FIRST_SUCCESS_EPOCH_MS=1210\n"
                "HRW_FIRST_REQUEST_DURATION_MS=10\n"
                "HRW_ATTEMPTS=1\n"
            )
            startup = build_lifecycle_measurement(
                pod, target_log, observer_log, timeout_seconds=120
            )
            raw_stats = {
                phase: {
                    "node": {
                        "cpu": {
                            "usageNanoCores": 100_000_000,
                            "time": f"2026-07-13T00:00:0{index}Z",
                        },
                        "memory": {"workingSetBytes": 1000},
                    },
                    "pods": [],
                }
                for index, phase in enumerate(("before", "after"))
            }
            validity = {
                "max_background_cpu_millicores": 2000,
                "max_background_memory_bytes": 8_000_000_000,
            }
            boundary = evaluate_lifecycle_boundaries(
                normalize_stats_sample(raw_stats["before"], "hrw-test", 0),
                normalize_stats_sample(raw_stats["after"], "hrw-test", 0),
                validity,
            )
            json_files = {
                "startup.json": startup,
                "target-pod.json": pod,
                "boundary-validity.json": boundary,
                "boundary-kubelet-stats.json": raw_stats,
            }
            result = {"startup": startup, "runtime_metrics": {}}
            json_files["result.json"] = result
            for name, value in json_files.items():
                (trial_dir / name).write_text(json.dumps(value))
            (trial_dir / "target.log").write_text(target_log)
            (trial_dir / "observer.log").write_text(observer_log)
            trial = {
                "trial_id": "trial-01",
                "status": "valid",
                "summary": build_trial_summary(result, "target-pod.json"),
            }
            (trial_dir / "trial.json").write_text(json.dumps(trial))
            artifact_paths = [*json_files, "target.log", "observer.log"]
            (trial_dir / "artifact-manifest.json").write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {"path": path} for path in sorted(artifact_paths)
                        ]
                    }
                )
            )
            run_set = {
                "trials": [
                    {
                        "trial_id": "trial-01",
                        "status": "valid",
                        "path": "trials/01/trial.json",
                    }
                ],
                "summary": summarize_trials(
                    [{**trial, "status": "valid", "result": result}]
                ),
                "platform_evidence": {
                    "image_prepull": {"path": "image-prepull.json"}
                },
            }
            (run_set_dir / "run-set.json").write_text(json.dumps(run_set))
            prepull_pod = {
                "spec": {
                    "containers": [
                        {
                            "name": "observer",
                            "image": observer_image,
                            "imagePullPolicy": "IfNotPresent",
                        },
                        {
                            "name": "target",
                            "image": target_image,
                            "imagePullPolicy": "IfNotPresent",
                        },
                    ]
                },
                "status": {
                    "phase": "Succeeded",
                    "containerStatuses": [
                        {
                            "name": "observer",
                            "imageID": observer_image,
                            "restartCount": 0,
                        },
                        {
                            "name": "target",
                            "imageID": target_image,
                            "restartCount": 0,
                        },
                    ],
                },
            }
            (run_set_dir / "image-prepull.json").write_text(
                json.dumps(
                    build_prepull_evidence(
                        prepull_pod,
                        target_image=target_image,
                        observer_image=observer_image,
                    )
                )
            )
            (run_set_dir / "environment.yaml").write_text(
                "images:\n"
                f"  k6: {observer_image}\n"
                "validity:\n"
                "  max_background_cpu_millicores: 2000\n"
                "  max_background_memory_bytes: 8000000000\n"
            )
            manifest = {
                "cohort": {"evidence_family": "lifecycle"},
                "selection": {
                    "environment_profile": "home-k3s-lifecycle-v1"
                },
                "contracts": {
                    "environment_profile": {"path": "environment.yaml"}
                },
                "execution": {
                    "image_tag": target_image,
                    "startup": {"timeout_seconds": 120},
                },
            }
            (run_set_dir / "resolved-manifest.json").write_text(json.dumps(manifest))

            validate_lifecycle_publication_evidence(run_set_dir, run_set_dir)
            raw = json_files["boundary-kubelet-stats.json"]
            raw["after"]["node"]["cpu"]["usageNanoCores"] = 3_000_000_000
            (trial_dir / "boundary-kubelet-stats.json").write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "boundary evidence"):
                validate_lifecycle_publication_evidence(run_set_dir, run_set_dir)
            raw["after"]["node"]["cpu"]["usageNanoCores"] = 100_000_000
            (trial_dir / "boundary-kubelet-stats.json").write_text(
                json.dumps(raw)
            )
            startup["ready_ms"] = 999
            (trial_dir / "startup.json").write_text(json.dumps(startup))
            with self.assertRaisesRegex(ValueError, "startup evidence"):
                validate_lifecycle_publication_evidence(run_set_dir, run_set_dir)
            startup["ready_ms"] = 210
            (trial_dir / "startup.json").write_text(json.dumps(startup))
            published_result = json.loads((trial_dir / "result.json").read_text())
            published_result["startup"]["ready_ms"] = 999
            (trial_dir / "result.json").write_text(json.dumps(published_result))
            with self.assertRaisesRegex(ValueError, "result startup"):
                validate_lifecycle_publication_evidence(run_set_dir, run_set_dir)
            (trial_dir / "result.json").write_text(json.dumps(result))
            changed_trial = json.loads((trial_dir / "trial.json").read_text())
            changed_trial["summary"][0]["value"] = 999
            (trial_dir / "trial.json").write_text(json.dumps(changed_trial))
            with self.assertRaisesRegex(ValueError, "trial-01 summary"):
                validate_lifecycle_publication_evidence(run_set_dir, run_set_dir)
            (trial_dir / "trial.json").write_text(json.dumps(trial))
            changed_run_set = json.loads((run_set_dir / "run-set.json").read_text())
            changed_run_set["summary"]["valid_trial_count"] = 0
            (run_set_dir / "run-set.json").write_text(json.dumps(changed_run_set))
            with self.assertRaisesRegex(ValueError, "run-set summary"):
                validate_lifecycle_publication_evidence(run_set_dir, run_set_dir)

    def test_skips_kubernetes_raw_checks_for_local_compose_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "run-set.json").write_text('{"trials": []}')
            (root / "resolved-manifest.json").write_text(
                json.dumps(
                    {
                        "cohort": {"evidence_family": "lifecycle"},
                        "selection": {
                            "environment_profile": "local-docker-compose"
                        },
                    }
                )
            )

            validate_lifecycle_publication_evidence(root, root)

    def test_lifecycle_requires_image_prepull_platform_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_set_dir = Path(temp_dir)
            for name in ("preflight", "postflight", "build"):
                (run_set_dir / f"{name}.json").write_text('{"ok": true}\n')
            (run_set_dir / "resolved-manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-set-id",
                        "manifest_digest": DIGEST_A,
                        "selection": {
                            "environment_profile": "home-k3s-lifecycle-v1"
                        },
                        "cohort": {
                            "fingerprint": DIGEST_B,
                            "evidence_family": "lifecycle",
                        },
                    }
                )
            )
            (run_set_dir / "run-set.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_set_id": "run-set-id",
                        "run_id": "run-set-id",
                        "status": "complete",
                        "started_at": "2026-07-12T00:00:00Z",
                        "finished_at": "2026-07-12T00:00:01Z",
                        "manifest_digest": DIGEST_A,
                        "cohort_fingerprint": DIGEST_B,
                        "expected_trials": 1,
                        "trials": [
                            {
                                "trial_id": "trial-01",
                                "index": 1,
                                "status": "valid",
                                "path": "trials/01/trial.json",
                                "sha256": DIGEST_A,
                            }
                        ],
                        "summary": {
                            "trial_count": 1,
                            "valid_trial_count": 1,
                            "runtime_metrics": {},
                            "startup_metrics": {},
                        },
                        "platform_evidence": {
                            name: {
                                "path": f"{name}.json",
                                "sha256": sha256_file(
                                    run_set_dir / f"{name}.json"
                                ),
                            }
                            for name in ("preflight", "postflight", "build")
                        },
                    }
                )
            )

            with self.assertRaisesRegex(ValueError, "image_prepull"):
                validate_run_set_evidence(run_set_dir, PROJECT_ROOT)

    def test_verifies_the_complete_digest_chain_and_raw_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_set_dir = Path(temp_dir)
            trial_dir = run_set_dir / "trials/01"
            trial_dir.mkdir(parents=True)
            (run_set_dir / "resolved-manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-set-id",
                        "manifest_digest": DIGEST_A,
                        "cohort": {"fingerprint": DIGEST_B},
                    }
                )
            )
            for name in ("preflight", "postflight", "build"):
                (run_set_dir / f"{name}.json").write_text('{"ok": true}\n')
            (trial_dir / "k6-summary.json").write_text('{"metric": 1}\n')
            time_series = build_compact_time_series("trial-01", 1, [])
            (trial_dir / "time-series.json").write_text(json.dumps(time_series))
            artifacts = build_artifact_manifest("trial-01", trial_dir)
            (trial_dir / "artifact-manifest.json").write_text(json.dumps(artifacts))
            trial = {
                "schema_version": "1.0",
                "run_id": "run-set-id",
                "trial_id": "trial-01",
                "manifest_digest": DIGEST_A,
                "cohort_fingerprint": DIGEST_B,
                "status": "valid",
                "started_at": "2026-07-12T00:00:00Z",
                "finished_at": "2026-07-12T00:00:01Z",
                "summary": [
                    {
                        "name": "rps",
                        "unit": "requests_per_second",
                        "value": 1,
                        "source_artifacts": ["k6-summary.json"],
                    }
                ],
                "time_series": {
                    "path": "time-series.json",
                    "sha256": sha256_file(trial_dir / "time-series.json"),
                },
                "artifact_manifest": {
                    "path": "artifact-manifest.json",
                    "sha256": sha256_file(trial_dir / "artifact-manifest.json"),
                },
            }
            (trial_dir / "trial.json").write_text(json.dumps(trial))
            run_set = {
                "schema_version": "1.0",
                "run_set_id": "run-set-id",
                "run_id": "run-set-id",
                "status": "complete",
                "started_at": "2026-07-12T00:00:00Z",
                "finished_at": "2026-07-12T00:00:01Z",
                "manifest_digest": DIGEST_A,
                "cohort_fingerprint": DIGEST_B,
                "expected_trials": 1,
                "trials": [
                    {
                        "trial_id": "trial-01",
                        "index": 1,
                        "status": "valid",
                        "path": "trials/01/trial.json",
                        "sha256": sha256_file(trial_dir / "trial.json"),
                    }
                ],
                "summary": {
                    "trial_count": 1,
                    "valid_trial_count": 1,
                    "runtime_metrics": {},
                    "startup_metrics": {},
                },
                "platform_evidence": {
                    name: {
                        "path": f"{name}.json",
                        "sha256": sha256_file(run_set_dir / f"{name}.json"),
                    }
                    for name in ("preflight", "postflight", "build")
                },
            }
            (run_set_dir / "run-set.json").write_text(json.dumps(run_set))

            validate_run_set_evidence(run_set_dir, PROJECT_ROOT)
            (trial_dir / "k6-summary.json").write_text("tampered\n")

            with self.assertRaisesRegex(ValueError, "Artifact (size|digest) mismatch"):
                validate_run_set_evidence(run_set_dir, PROJECT_ROOT)

            (trial_dir / "k6-summary.json").write_text('{"metric": 1}\n')
            (run_set_dir / "preflight.json").write_text('{"ok": false}\n')
            with self.assertRaisesRegex(ValueError, "Platform evidence digest mismatch"):
                validate_run_set_evidence(run_set_dir, PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
