import unittest
from pathlib import Path

from hrw_runner.kubernetes_render import render_ping_documents


ROOT = Path(__file__).resolve().parents[2]


class KubernetesRenderTest(unittest.TestCase):
    def test_structurally_renders_runner_values_and_scenario_script(self):
        documents = render_ping_documents(
            ROOT / "infra/k8s/ping-api.yaml",
            namespace="hrw-test",
            run_set_id="run-set-id",
            target_image="ghcr.io/example/target@sha256:" + "a" * 64,
            k6_image="grafana/k6@sha256:" + "b" * 64,
            java_tool_options="-XX:MaxRAMPercentage=75",
            duration="30s",
            vus=50,
            job_name="k6-measured-01",
            script="export default function () { /* pong */ }\n",
            virtual_threads=True,
        )

        by_kind = {document["kind"]: document for document in documents}
        self.assertEqual(by_kind["Namespace"]["metadata"]["name"], "hrw-test")
        self.assertEqual(
            by_kind["Pod"]["spec"]["containers"][0]["image"],
            "ghcr.io/example/target@sha256:" + "a" * 64,
        )
        self.assertEqual(by_kind["ConfigMap"]["data"]["k6.js"], "export default function () { /* pong */ }\n")
        self.assertEqual(by_kind["Job"]["metadata"]["name"], "k6-measured-01")
        target_env = {
            item["name"]: item["value"]
            for item in by_kind["Pod"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(target_env["SPRING_THREADS_VIRTUAL_ENABLED"], "true")
        self.assertEqual(target_env["SPRING_MAIN_KEEP_ALIVE"], "true")
        self.assertNotIn("__", repr(documents))

    def test_allows_k6_standard_env_identifier_inside_the_script(self):
        documents = render_ping_documents(
            ROOT / "infra/k8s/ping-api.yaml",
            namespace="hrw-test",
            run_set_id="run-set-id",
            target_image="target@sha256:" + "a" * 64,
            k6_image="k6@sha256:" + "b" * 64,
            java_tool_options="-Xmx512m",
            duration="30s",
            vus=50,
            job_name="k6-measured",
            script="const baseUrl = __ENV.BASE_URL;\n",
        )

        config_map = next(item for item in documents if item["kind"] == "ConfigMap")
        self.assertIn("__ENV.BASE_URL", config_map["data"]["k6.js"])

    def test_rejects_non_dns_names_and_mutable_images(self):
        cases = (
            {"namespace": "UPPER"},
            {"job_name": "bad_name"},
            {"target_image": "ghcr.io/example/target:latest"},
            {"k6_image": "grafana/k6:latest"},
        )
        defaults = {
            "namespace": "hrw-test",
            "run_set_id": "run-set-id",
            "target_image": "target@sha256:" + "a" * 64,
            "k6_image": "k6@sha256:" + "b" * 64,
            "java_tool_options": "-Xmx512m",
            "duration": "30s",
            "vus": 50,
            "job_name": "k6-measured",
            "script": "export default function () {}\n",
        }
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    render_ping_documents(
                        ROOT / "infra/k8s/ping-api.yaml",
                        **{**defaults, **changes},
                    )


if __name__ == "__main__":
    unittest.main()
