import unittest
from pathlib import Path

from hrw_runner.kubernetes_render import render_ping_documents, render_scenario_documents


ROOT = Path(__file__).resolve().parents[2]


class KubernetesRenderTest(unittest.TestCase):
    def test_renders_postgres_init_asset_for_read_heavy_scenario(self):
        init_sql = "create table catalog_products (id bigint primary key);\n"

        documents = render_scenario_documents(
            ROOT / "infra/k8s/read-heavy-query-api.yaml",
            namespace="hrw-test",
            run_set_id="run-set-id",
            target_image="target@sha256:" + "a" * 64,
            k6_image="k6@sha256:" + "b" * 64,
            java_tool_options="-Xmx512m",
            duration="30s",
            vus=50,
            job_name="k6-measured",
            script="export default function () {}\n",
            postgres_init_sql=init_sql,
            target_environment={},
        )

        config_map = next(
            item for item in documents if item["metadata"]["name"] == "postgres-init"
        )
        self.assertEqual(config_map["data"]["init.sql"], init_sql)
        self.assertNotIn("__POSTGRES_INIT_SQL__", repr(documents))

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
            target_environment={
                "Z_FRAMEWORK_SETTING": "last",
                "A_FRAMEWORK_SETTING": "first",
            },
        )

        by_kind = {document["kind"]: document for document in documents}
        self.assertEqual(by_kind["Namespace"]["metadata"]["name"], "hrw-test")
        self.assertEqual(
            by_kind["Pod"]["spec"]["containers"][0]["image"],
            "ghcr.io/example/target@sha256:" + "a" * 64,
        )
        self.assertEqual(by_kind["ConfigMap"]["data"]["k6.js"], "export default function () { /* pong */ }\n")
        self.assertEqual(by_kind["Job"]["metadata"]["name"], "k6-measured-01")
        self.assertEqual(
            by_kind["Pod"]["spec"]["containers"][0]["env"],
            [
                {"name": "JAVA_TOOL_OPTIONS", "value": "-XX:MaxRAMPercentage=75"},
                {"name": "A_FRAMEWORK_SETTING", "value": "first"},
                {"name": "Z_FRAMEWORK_SETTING", "value": "last"},
            ],
        )
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
            target_environment={},
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
            "target_environment": {},
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
