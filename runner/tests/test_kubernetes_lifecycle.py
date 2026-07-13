import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from hrw_runner.kubernetes_lifecycle import (
    LifecycleEvidenceError,
    build_prepull_evidence,
    build_lifecycle_measurement,
    evaluate_lifecycle_boundaries,
    render_lifecycle_documents,
    validate_lifecycle_pod,
)
from hrw_runner.k3s_runner import (
    _collect_lifecycle_failure_evidence,
    _prepull_target,
)


ROOT = Path(__file__).resolve().parents[2]


class LifecycleRenderTest(unittest.TestCase):
    def test_renders_native_sidecar_without_overriding_target_entrypoint(self):
        documents = render_lifecycle_documents(
            ROOT / "infra/k8s/cold-start-api.yaml",
            namespace="hrw-test",
            run_set_id="run-set-id",
            target_image="ghcr.io/example/target@sha256:" + "a" * 64,
            observer_image="grafana/k6@sha256:" + "b" * 64,
            java_tool_options="-XX:MaxRAMPercentage=75",
            timeout_seconds=120,
            poll_interval_ms=10,
            request_timeout_ms=250,
            target_environment={"EXAMPLE_SETTING": "value"},
        )

        target = next(item for item in documents if item["kind"] == "Pod")
        observer = target["spec"]["initContainers"][0]
        container = target["spec"]["containers"][0]

        self.assertNotIn("command", container)
        self.assertNotIn("args", container)
        self.assertNotIn("readinessProbe", container)
        self.assertEqual(observer["restartPolicy"], "Always")
        self.assertEqual(
            observer["startupProbe"]["exec"]["command"],
            ["/bin/sh", "-c", "test -f /evidence/armed"],
        )
        command = observer["args"][0]
        self.assertNotIn("touch /evidence/armed &&", command)
        marker_index = command.index("HRW_OBSERVER_READY_EPOCH_MS=")
        self.assertGreater(command.rindex("/evidence/armed"), marker_index)
        success_index = command.index("HRW_FIRST_SUCCESS_EPOCH_MS=")
        self.assertGreater(command.rindex("/evidence/success"), success_index)
        self.assertEqual(
            observer["readinessProbe"]["exec"]["command"],
            ["/bin/sh", "-c", "test -f /evidence/success"],
        )
        config_map = next(item for item in documents if item["kind"] == "ConfigMap")
        self.assertIn("http://127.0.0.1:8080/ping", config_map["data"]["observer.js"])
        self.assertNotIn("__", repr(documents))


class LifecycleMarkerTest(unittest.TestCase):
    def test_computes_process_to_first_success_and_request_duration(self):
        startup = build_lifecycle_measurement(
            {
                "status": {
                    "containerStatuses": [
                        {
                            "name": "target",
                            "state": {
                                "running": {
                                    "startedAt": "1970-01-01T00:00:01Z"
                                }
                            },
                        }
                    ]
                }
            },
            "HRW_ENTRYPOINT_PRE_EXEC_EPOCH_MS=1000\n",
            'time="now" level=info msg="HRW_OBSERVER_READY_EPOCH_MS=900"\n'
            "HRW_FIRST_REQUEST_START_EPOCH_MS=1220\n"
            "HRW_FIRST_SUCCESS_EPOCH_MS=1234\n"
            "HRW_FIRST_REQUEST_DURATION_MS=14\n"
            "HRW_ATTEMPTS=3\n",
            timeout_seconds=120,
        )

        self.assertEqual(startup["ready_ms"], 234)
        self.assertEqual(startup["entrypoint_pre_exec_to_first_valid_response_ms"], 234)
        self.assertEqual(startup["first_request_ms"], 14)
        self.assertEqual(startup["entrypoint_pre_exec_epoch_ms"], 1000)
        self.assertEqual(startup["first_success_epoch_ms"], 1234)
        self.assertEqual(startup["attempts"], 3)

    def test_rejects_duplicate_missing_or_reversed_markers(self):
        pod = {
            "status": {
                "containerStatuses": [
                    {
                        "name": "target",
                        "state": {"running": {"startedAt": "1970-01-01T00:00:02Z"}},
                    }
                ]
            }
        }
        cases = (
            "HRW_FIRST_SUCCESS_EPOCH_MS=1234\n",
            "HRW_OBSERVER_READY_EPOCH_MS=900\n"
            "HRW_FIRST_REQUEST_START_EPOCH_MS=1200\n"
            "HRW_FIRST_SUCCESS_EPOCH_MS=1234\n"
            "HRW_FIRST_SUCCESS_EPOCH_MS=1235\n"
            "HRW_FIRST_REQUEST_DURATION_MS=34\nHRW_ATTEMPTS=1\n",
            "HRW_OBSERVER_READY_EPOCH_MS=900\n"
            "HRW_FIRST_REQUEST_START_EPOCH_MS=1200\n"
            "HRW_FIRST_SUCCESS_EPOCH_MS=1234\n"
            "HRW_FIRST_REQUEST_DURATION_MS=34\nHRW_ATTEMPTS=1\n",
        )
        for observer_log in cases:
            with self.subTest(observer_log=observer_log):
                with self.assertRaises(LifecycleEvidenceError):
                    build_lifecycle_measurement(
                        pod,
                        "HRW_ENTRYPOINT_PRE_EXEC_EPOCH_MS=2000\n",
                        observer_log,
                        timeout_seconds=120,
                    )

    def test_checks_request_duration_against_response_completion(self):
        pod = {
            "status": {
                "containerStatuses": [
                    {
                        "name": "target",
                        "state": {
                            "running": {"startedAt": "1970-01-01T00:00:01Z"}
                        },
                    }
                ]
            }
        }
        observer_log = (
            "HRW_OBSERVER_READY_EPOCH_MS=900\n"
            "HRW_FIRST_REQUEST_START_EPOCH_MS=1220\n"
            "HRW_FIRST_SUCCESS_EPOCH_MS=1234\n"
            "HRW_FIRST_REQUEST_DURATION_MS=13\n"
            "HRW_ATTEMPTS=1\n"
        )

        with self.assertRaisesRegex(LifecycleEvidenceError, "duration"):
            build_lifecycle_measurement(
                pod,
                "HRW_ENTRYPOINT_PRE_EXEC_EPOCH_MS=1000\n",
                observer_log,
                timeout_seconds=120,
            )


class LifecycleBoundaryValidityTest(unittest.TestCase):
    def test_rejects_background_load_at_either_trial_boundary(self):
        validity = {
            "max_background_cpu_millicores": 2000,
            "max_background_memory_bytes": 8_000_000_000,
        }
        before = {
            "background_cpu_millicores": 100,
            "background_memory_bytes": 1_000,
        }
        after = {
            "background_cpu_millicores": 2100,
            "background_memory_bytes": 1_000,
        }

        result = evaluate_lifecycle_boundaries(before, after, validity)

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(
            [sample["phase"] for sample in result["samples"]],
            ["before", "after"],
        )
        self.assertTrue(
            any("background CPU" in reason for reason in result["reasons"])
        )


class LifecyclePrepullTest(unittest.TestCase):
    def test_prepulls_target_and_observer_before_never_pull_trials(self):
        client = Mock()
        client.json.return_value = {
            "spec": {
                "containers": [
                    {
                        "name": "observer",
                        "image": "example/observer@sha256:" + "a" * 64,
                        "imagePullPolicy": "IfNotPresent",
                    },
                    {
                        "name": "target",
                        "image": "target@sha256:" + "b" * 64,
                        "imagePullPolicy": "IfNotPresent",
                    },
                ]
            },
            "status": {
                "phase": "Succeeded",
                "containerStatuses": [
                    {
                        "name": "observer",
                        "imageID": "docker.io/example/observer@sha256:" + "a" * 64,
                        "restartCount": 0,
                    },
                    {
                        "name": "target",
                        "imageID": "target@sha256:" + "b" * 64,
                        "restartCount": 0,
                    },
                ],
            },
        }
        target = {
            "metadata": {"name": "target"},
            "spec": {
                "restartPolicy": "Never",
                "initContainers": [
                    {
                        "name": "observer",
                        "image": "example/observer@sha256:" + "a" * 64,
                        "imagePullPolicy": "Never",
                        "restartPolicy": "Always",
                        "startupProbe": {"exec": {"command": ["true"]}},
                    }
                ],
                "containers": [
                    {
                        "name": "target",
                        "image": "target@sha256:" + "b" * 64,
                        "imagePullPolicy": "Never",
                    }
                ],
            },
        }

        pod = _prepull_target(client, target, "hrw-test")

        applied = client.apply.call_args.args[0][0]
        self.assertNotIn("initContainers", applied["spec"])
        self.assertEqual(
            [container["name"] for container in applied["spec"]["containers"]],
            ["observer", "target"],
        )
        self.assertTrue(
            all(
                container["imagePullPolicy"] == "IfNotPresent"
                for container in applied["spec"]["containers"]
            )
        )
        self.assertEqual(
            build_prepull_evidence(
                pod,
                target_image="target@sha256:" + "b" * 64,
                observer_image="example/observer@sha256:" + "a" * 64,
            )["status"],
            "valid",
        )

    def test_rejects_prepull_expected_image_drift(self):
        pod = {
            "spec": {
                "containers": [
                    {
                        "name": "observer",
                        "image": "observer@sha256:" + "a" * 64,
                        "imagePullPolicy": "IfNotPresent",
                    },
                    {
                        "name": "target",
                        "image": "target@sha256:" + "b" * 64,
                        "imagePullPolicy": "IfNotPresent",
                    },
                ]
            },
            "status": {
                "phase": "Succeeded",
                "containerStatuses": [
                    {
                        "name": "observer",
                        "imageID": "observer@sha256:" + "a" * 64,
                        "restartCount": 0,
                    },
                    {
                        "name": "target",
                        "imageID": "target@sha256:" + "b" * 64,
                        "restartCount": 0,
                    },
                ],
            },
        }

        evidence = build_prepull_evidence(
            pod,
            target_image="target@sha256:" + "c" * 64,
            observer_image="observer@sha256:" + "a" * 64,
        )

        self.assertEqual(evidence["status"], "invalid")
        self.assertTrue(any("target image" in reason for reason in evidence["reasons"]))

    def test_rejects_observer_restart_or_image_drift(self):
        pod = {
            "status": {
                "containerStatuses": [
                    {
                        "name": "target",
                        "imageID": "target@sha256:" + "a" * 64,
                        "restartCount": 0,
                        "state": {"running": {}},
                    }
                ],
                "initContainerStatuses": [
                    {
                        "name": "observer",
                        "imageID": "observer@sha256:" + "c" * 64,
                        "restartCount": 1,
                        "state": {"running": {}},
                    }
                ],
            }
        }

        reasons = validate_lifecycle_pod(
            pod,
            target_image="target@sha256:" + "a" * 64,
            observer_image="observer@sha256:" + "b" * 64,
        )

        self.assertTrue(any("observer imageID" in reason for reason in reasons))
        self.assertTrue(any("observer restarted" in reason for reason in reasons))


class LifecycleFailureEvidenceTest(unittest.TestCase):
    def test_collects_pod_logs_and_events_before_cleanup(self):
        client = Mock()
        client.json.side_effect = [
            {"metadata": {"name": "target"}, "status": {"phase": "Failed"}},
            {"items": [{"reason": "BackOff"}]},
        ]
        client.command.side_effect = ["observer failed\n", "target failed\n"]

        with tempfile.TemporaryDirectory() as temp_dir:
            trial_dir = Path(temp_dir)
            _collect_lifecycle_failure_evidence(client, "hrw-test", trial_dir)

            self.assertTrue((trial_dir / "target-pod-failure.json").is_file())
            self.assertEqual(
                (trial_dir / "observer-failure.log").read_text(),
                "observer failed\n",
            )
            self.assertEqual(
                (trial_dir / "target-failure.log").read_text(),
                "target failed\n",
            )
            self.assertTrue((trial_dir / "target-events.json").is_file())


if __name__ == "__main__":
    unittest.main()
