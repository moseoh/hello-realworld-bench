import json
import os
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hrw_runner.config import resolve_run_config
from hrw_runner.k3s_runner import (
    _collect_dependency_evidence,
    _pod_failure_reasons,
    _read_dataset_init_sql,
    _summary_from_k6_log,
    _reset_scenario_state,
    _scenario_correctness,
    _wait_job,
    _write_failed_trial,
    run_k3s_benchmark_set,
)
from hrw_runner.kubernetes import evaluate_preflight


PROFILE = {
    "cluster": {
        "context": "homelab",
        "node_name": "homlab",
        "machine_id": "f66cd2d134b94bb18eb7e531d1baf343",
        "architecture": "amd64",
        "cpu_manager_policy": "none",
        "min_logical_cpus": 16,
        "min_memory_bytes": 28_000_000_000,
    },
    "validity": {
        "max_background_cpu_millicores": 2000,
        "max_background_memory_bytes": 8_000_000_000,
        "min_sample_coverage_ratio": 0.9,
    },
}


class StopAfterImageResolution(Exception):
    pass


class K3sImageConfigurationTest(unittest.TestCase):
    def test_build_uses_the_implementation_official_repository(self):
        config = self._config()
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = SimpleNamespace(
                result_dir=Path(temp_dir) / "result",
                run_id="run-id",
            )
            with (
                patch.dict(os.environ, {"HRW_IMAGE_DISTRIBUTION": "push"}, clear=True),
                patch("hrw_runner.k3s_runner._run_set_paths", return_value=paths),
                patch(
                    "hrw_runner.k3s_runner.read_git_provenance",
                    return_value={"git_commit": "a" * 40},
                ),
                patch(
                    "hrw_runner.k3s_runner.evaluate_preflight",
                    return_value={"status": "valid", "reasons": []},
                ),
                patch("hrw_runner.k3s_runner.Kubectl"),
                patch(
                    "hrw_runner.k3s_runner.build_and_push_image",
                    side_effect=StopAfterImageResolution,
                ) as build,
            ):
                with self.assertRaises(StopAfterImageResolution):
                    run_k3s_benchmark_set(config, Path(temp_dir))

        build.assert_called_once_with(
            config.app_dir,
            config.official_image_repository,
            "a" * 40,
            "25",
        )

    def test_prebuilt_validation_uses_the_implementation_official_repository(self):
        config = self._config()
        image = config.official_image_repository + "@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = SimpleNamespace(
                result_dir=Path(temp_dir) / "result",
                run_id="run-id",
            )
            with (
                patch.dict(os.environ, {"HRW_TARGET_IMAGE": image}, clear=True),
                patch("hrw_runner.k3s_runner._run_set_paths", return_value=paths),
                patch(
                    "hrw_runner.k3s_runner.read_git_provenance",
                    return_value={"git_commit": "a" * 40},
                ),
                patch(
                    "hrw_runner.k3s_runner.evaluate_preflight",
                    return_value={"status": "valid", "reasons": []},
                ),
                patch("hrw_runner.k3s_runner.Kubectl"),
                patch("hrw_runner.k3s_runner.replace", return_value=config),
                patch(
                    "hrw_runner.k3s_runner.build_resolved_manifest",
                    side_effect=StopAfterImageResolution,
                ),
            ):
                with self.assertRaises(StopAfterImageResolution):
                    run_k3s_benchmark_set(config, Path(temp_dir))

    def test_prebuilt_rejects_digest_unless_exactly_64_lowercase_hex(self):
        config = self._config()
        for digest in ("a" * 63, "a" * 65, "A" * 64, "g" * 64):
            with self.subTest(digest=digest), tempfile.TemporaryDirectory() as temp_dir:
                paths = SimpleNamespace(
                    result_dir=Path(temp_dir) / "result",
                    run_id="run-id",
                )
                image = config.official_image_repository + "@sha256:" + digest
                with (
                    patch.dict(os.environ, {"HRW_TARGET_IMAGE": image}, clear=True),
                    patch("hrw_runner.k3s_runner._run_set_paths", return_value=paths),
                    patch(
                        "hrw_runner.k3s_runner.read_git_provenance",
                        return_value={"git_commit": "a" * 40},
                    ),
                    patch(
                        "hrw_runner.k3s_runner.evaluate_preflight",
                        return_value={"status": "valid", "reasons": []},
                    ),
                    patch("hrw_runner.k3s_runner.Kubectl"),
                    patch("hrw_runner.k3s_runner.replace", return_value=config),
                    patch(
                        "hrw_runner.k3s_runner.build_resolved_manifest",
                        side_effect=StopAfterImageResolution,
                    ),
                ):
                    try:
                        with self.assertRaisesRegex(
                            ValueError,
                            "official immutable repository",
                        ):
                            run_k3s_benchmark_set(config, Path(temp_dir))
                    except StopAfterImageResolution:
                        self.fail("invalid prebuilt digest was accepted")

    def _config(self):
        config = Mock()
        config.scenario = "ping-api"
        config.app_dir = Path("/tmp/example-app")
        config.official_image_repository = "ghcr.io/example/implementation"
        config.environment_profile_config = {
            "cluster": {"context": "homelab"},
            "images": {"k6": "k6@sha256:" + "c" * 64},
        }
        config.runtime = {"java_version": "25"}
        return config


class ScenarioLifecycleTest(unittest.TestCase):
    def test_dataset_init_asset_must_stay_inside_scenario_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scenario_dir = root / "scenarios/read-heavy-query-api"
            scenario_dir.mkdir(parents=True)
            outside = root / "outside.sql"
            outside.write_text("select 1;\n")
            config = SimpleNamespace(
                root_dir=root,
                scenario_dir=scenario_dir,
                scenario_config={"dataset": {"asset": "outside.sql"}},
            )

            with self.assertRaisesRegex(ValueError, "scenario file"):
                _read_dataset_init_sql(config)

    def test_reads_dataset_init_asset_from_scenario_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scenario_dir = root / "scenarios/read-heavy-query-api/postgres"
            scenario_dir.mkdir(parents=True)
            asset = scenario_dir / "init.sql"
            asset.write_text("select 1;\n")
            config = SimpleNamespace(
                root_dir=root,
                scenario_dir=scenario_dir.parent,
                scenario_config={
                    "dataset": {
                        "asset": "scenarios/read-heavy-query-api/postgres/init.sql"
                    }
                },
            )

            self.assertEqual(_read_dataset_init_sql(config), "select 1;\n")

    def test_read_heavy_correctness_matches_dataset_fingerprint_and_index(self):
        client = Mock()
        client.command.return_value = (
            "100000,5000050000,5049950000,399997276,95000,1\n"
        )
        scenario_config = {
            "dataset": {
                "row_count": 100000,
                "fingerprint": {
                    "id_sum": 5000050000,
                    "price_cents_sum": 5049950000,
                    "rating_basis_points_sum": 399997276,
                    "active_count": 95000,
                },
            },
            "query_contract": {"index": "idx_catalog_products_filter"},
        }

        correctness = _scenario_correctness(
            client,
            "hrw-run",
            "read-heavy-query-api",
            {},
            scenario_config,
        )

        self.assertEqual(correctness["status"], "valid")
        self.assertEqual(correctness["oracle"], "read-heavy-dataset-fingerprint")
        self.assertEqual(correctness["observed"]["index_count"], 1)
        command = client.command.call_args.args[0]
        self.assertIn("idx_catalog_products_filter", command[-1])

    def test_read_heavy_correctness_rejects_dataset_drift(self):
        client = Mock()
        client.command.return_value = (
            "99999,5000050000,5049950000,399997276,95000,0\n"
        )
        scenario_config = {
            "dataset": {
                "row_count": 100000,
                "fingerprint": {
                    "id_sum": 5000050000,
                    "price_cents_sum": 5049950000,
                    "rating_basis_points_sum": 399997276,
                    "active_count": 95000,
                },
            },
            "query_contract": {"index": "idx_catalog_products_filter"},
        }

        correctness = _scenario_correctness(
            client,
            "hrw-run",
            "read-heavy-query-api",
            {},
            scenario_config,
        )

        self.assertEqual(correctness["status"], "invalid")
        self.assertTrue(any("row_count" in reason for reason in correctness["reasons"]))
        self.assertTrue(any("index_count" in reason for reason in correctness["reasons"]))

    def test_resets_transactional_tables_after_warmup(self):
        client = Mock()

        _reset_scenario_state(client, "hrw-run", "transactional-command-api")

        command = client.command.call_args.args[0]
        self.assertEqual(command[:4], ["exec", "pod/postgres", "-n", "hrw-run"])
        self.assertIn("truncate table order_items, outbox_events, orders", command[-1])

    def test_transactional_correctness_matches_all_rows_to_iterations(self):
        client = Mock()
        client.command.return_value = "120,120,120\n"
        summary = {"metrics": {"iterations": {"values": {"count": 120}}}}

        correctness = _scenario_correctness(
            client, "hrw-run", "transactional-command-api", summary
        )

        self.assertEqual(correctness["status"], "valid")
        self.assertEqual(correctness["expected_iterations"], 120)
        self.assertEqual(
            correctness["observed"],
            {"orders": 120, "order_items": 120, "outbox_events": 120},
        )

    def test_transactional_correctness_rejects_missing_outbox_write(self):
        client = Mock()
        client.command.return_value = "120,120,119\n"
        summary = {"metrics": {"iterations": {"values": {"count": 120}}}}

        correctness = _scenario_correctness(
            client, "hrw-run", "transactional-command-api", summary
        )

        self.assertEqual(correctness["status"], "invalid")
        self.assertIn("outbox_events", correctness["reasons"][0])


class KubernetesPreflightTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.current_context.return_value = "homelab"
        self.client.json.side_effect = self._response

    def test_accepts_the_expected_idle_single_node_cluster(self):
        result = evaluate_preflight(self.client, PROFILE)

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["cluster"]["node_name"], "homlab")
        self.assertEqual(result["cluster"]["logical_cpus"], 16)
        self.assertEqual(result["cluster"]["cpu_manager_policy"], "none")
        self.assertEqual(result["background"]["cpu_millicores"], 125.0)
        self.assertEqual(result["background"]["memory_working_set_bytes"], 2_000_000_000)

    def test_rejects_wrong_context_before_reading_cluster_state(self):
        self.client.current_context.return_value = "production"

        result = evaluate_preflight(self.client, PROFILE)

        self.assertEqual(result["status"], "invalid")
        self.assertIn("expected kube context homelab", result["reasons"][0])
        self.client.json.assert_not_called()

    def test_rejects_noise_existing_run_namespace_and_node_contract_drift(self):
        def response(arguments):
            value = self._response(arguments)
            if arguments[:2] == ["get", "node"]:
                value["status"]["capacity"]["cpu"] = "8"
                value["status"]["conditions"][0]["status"] = "False"
            elif arguments[0] == "get" and arguments[1] == "namespaces":
                value["items"] = [{"metadata": {"name": "hrw-old"}}]
            elif arguments[0] == "get" and arguments[1] == "--raw":
                if arguments[2].endswith("/stats/summary"):
                    value["node"]["cpu"]["usageNanoCores"] = 3_000_000_000
                    value["node"]["memory"]["workingSetBytes"] = 9_000_000_000
            return value

        self.client.json.side_effect = response

        result = evaluate_preflight(self.client, PROFILE)

        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("not Ready" in reason for reason in result["reasons"]))
        self.assertTrue(any("logical CPUs" in reason for reason in result["reasons"]))
        self.assertTrue(any("benchmark namespace" in reason for reason in result["reasons"]))
        self.assertTrue(any("background CPU" in reason for reason in result["reasons"]))
        self.assertTrue(any("background memory" in reason for reason in result["reasons"]))

    def _response(self, arguments):
        if arguments[:2] == ["get", "node"]:
            return {
                "metadata": {"name": "homlab"},
                "status": {
                    "capacity": {"cpu": "16", "memory": "28654240Ki"},
                    "allocatable": {"cpu": "16", "memory": "28654240Ki"},
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {
                        "architecture": "amd64",
                        "machineID": "f66cd2d134b94bb18eb7e531d1baf343",
                        "kernelVersion": "6.8.0",
                        "osImage": "Ubuntu 24.04",
                        "containerRuntimeVersion": "containerd://2.3.2-k3s2",
                        "kubeletVersion": "v1.36.2+k3s1",
                    },
                },
            }
        if arguments[0] == "version":
            return {"serverVersion": {"gitVersion": "v1.36.2+k3s1"}}
        if arguments[0] == "get" and arguments[1] == "namespaces":
            return {"items": []}
        if arguments[0] == "get" and arguments[1] == "--raw":
            if arguments[2].endswith("/configz"):
                return {"kubeletconfig": {"cpuManagerPolicy": "none"}}
            if arguments[2].endswith("/stats/summary"):
                return {
                    "node": {
                        "cpu": {"usageNanoCores": 125_000_000},
                        "memory": {"workingSetBytes": 2_000_000_000},
                    }
                }
        raise AssertionError(arguments)


class K6SummaryLogTest(unittest.TestCase):
    def test_extracts_exactly_one_summary_marker(self):
        summary = _summary_from_k6_log(
            'progress\nHRW_SUMMARY_JSON={"metrics":{"checks":{"fails":0}}}\n'
        )

        self.assertEqual(summary["metrics"]["checks"]["fails"], 0)

    def test_rejects_missing_or_duplicate_markers(self):
        for log in ("progress\n", "HRW_SUMMARY_JSON={}\nHRW_SUMMARY_JSON={}\n"):
            with self.subTest(log=log):
                with self.assertRaisesRegex(RuntimeError, "one k6 summary marker"):
                    _summary_from_k6_log(log)


class FailedKubernetesTrialTest(unittest.TestCase):
    def test_persists_infrastructure_failure_as_schema_valid_evidence(self):
        root = Path(__file__).resolve().parents[2]
        config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            "jvm-java25",
            root,
            environment_profile="home-k3s-v1",
            measurement_protocol="official-service-v1",
            load_profile="platform-qualification-v1",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            trial_dir = Path(temp_dir) / "trial"
            trial = _write_failed_trial(
                config,
                "run-set-id",
                "trial-01",
                1,
                trial_dir,
                "a" * 64,
                "b" * 64,
                {},
                {},
                RuntimeError("job failed"),
            )

            artifact_paths = {
                item["path"]
                for item in json.loads(
                    (trial_dir / "artifact-manifest.json").read_text()
                )["artifacts"]
            }

        self.assertEqual(trial["status"], "failed")
        self.assertEqual(trial["invalidity_class"], "infrastructure")
        self.assertIn("error.json", artifact_paths)


class TargetImageIdentityTest(unittest.TestCase):
    def test_rejects_runtime_image_id_drift(self):
        expected = "ghcr.io/example/target@sha256:" + "a" * 64
        pod = {
            "status": {
                "containerStatuses": [
                    {
                        "imageID": "ghcr.io/example/target@sha256:" + "b" * 64,
                        "restartCount": 0,
                    }
                ]
            }
        }

        reasons = _pod_failure_reasons(pod, expected)

        self.assertTrue(any("imageID" in reason for reason in reasons))

    def test_rejects_target_oom_in_current_or_previous_container_state(self):
        expected = "ghcr.io/example/target@sha256:" + "a" * 64
        for state_name in ("state", "lastState"):
            with self.subTest(state_name=state_name):
                pod = {
                    "status": {
                        "containerStatuses": [
                            {
                                "imageID": expected,
                                "restartCount": 0,
                                state_name: {"terminated": {"reason": "OOMKilled"}},
                            }
                        ]
                    }
                }

                reasons = _pod_failure_reasons(pod, expected)

                self.assertIn("target was OOMKilled", reasons)


class DependencyPodEvidenceTest(unittest.TestCase):
    def test_rejects_dependency_oom_in_current_or_previous_container_state(self):
        for state_name in ("state", "lastState"):
            with self.subTest(state_name=state_name):
                client = Mock()
                client.json.return_value = {
                    "items": [
                        {
                            "metadata": {"name": "postgres"},
                            "status": {
                                "containerStatuses": [
                                    {
                                        "restartCount": 0,
                                        state_name: {
                                            "terminated": {"reason": "OOMKilled"}
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
                client.command.return_value = "dependency log"

                with tempfile.TemporaryDirectory() as temp_dir:
                    _, reasons = _collect_dependency_evidence(
                        client, "hrw-run", Path(temp_dir)
                    )

                self.assertIn("dependency postgres was OOMKilled", reasons)


class KubernetesJobWaitTest(unittest.TestCase):
    def test_fails_immediately_when_the_job_reports_failure(self):
        client = Mock()
        client.json.return_value = {
            "status": {
                "failed": 1,
                "conditions": [
                    {"type": "Failed", "reason": "BackoffLimitExceeded"}
                ],
            }
        }

        with self.assertRaisesRegex(RuntimeError, "BackoffLimitExceeded"):
            _wait_job(client, "namespace", "k6-measured", "480s")


if __name__ == "__main__":
    unittest.main()
