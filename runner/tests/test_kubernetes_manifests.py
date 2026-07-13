from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "infra" / "k8s" / "ping-api.yaml"
TRANSACTIONAL_MANIFEST = (
    ROOT / "infra" / "k8s" / "transactional-command-api.yaml"
)
READ_HEAVY_MANIFEST = ROOT / "infra" / "k8s" / "read-heavy-query-api.yaml"
READ_HEAVY_COMPOSE = ROOT / "infra" / "docker-compose.read-heavy-query-api.yml"
IO_AGGREGATION_MANIFEST = ROOT / "infra" / "k8s" / "io-aggregation-api.yaml"
DOCKERFILE = ROOT / "implementations" / "java" / "spring-boot" / "Dockerfile"
ENVIRONMENT = ROOT / "contracts" / "environment-profiles" / "home-k3s-v1.yaml"


class KubernetesManifestTest(unittest.TestCase):
    def setUp(self):
        self.rendered = MANIFEST.read_text()
        self.manifests = list(yaml.safe_load_all(self.rendered))

    def test_parses_and_contains_expected_objects(self):
        self.assertEqual(
            [manifest["kind"] for manifest in self.manifests],
            ["Namespace", "Service", "Pod", "ConfigMap", "Job"],
        )

    def test_has_runner_placeholders_and_common_labels(self):
        self.assertTrue(
            {
                "__NAMESPACE__",
                "__RUN_SET_ID__",
                "__TARGET_IMAGE__",
                "__TARGET_ENV__",
                "__K6_IMAGE__",
                "__K6_DURATION__",
                "__K6_VUS__",
                "__K6_JOB_NAME__",
                "__K6_SCRIPT__",
            }.issubset(self.rendered.split())
        )
        for manifest in self.manifests:
            with self.subTest(kind=manifest["kind"]):
                labels = manifest["metadata"]["labels"]
                self.assertEqual(
                    labels["app.kubernetes.io/part-of"], "hello-realworld-bench"
                )
                self.assertEqual(labels["hello-real-world/run-set"], "__RUN_SET_ID__")

    def test_workloads_use_guaranteed_resources_and_homlab_node(self):
        environment = yaml.safe_load(ENVIRONMENT.read_text())
        workloads = [
            manifest
            for manifest in self.manifests
            if manifest["kind"] in {"Pod", "Job"}
        ]
        for workload in workloads:
            with self.subTest(kind=workload["kind"]):
                pod_spec = (
                    workload["spec"]
                    if workload["kind"] == "Pod"
                    else workload["spec"]["template"]["spec"]
                )
                self.assertEqual(
                    pod_spec["nodeSelector"],
                    {
                        "kubernetes.io/hostname": environment["cluster"]["node_name"]
                    },
                )
                self.assertIs(pod_spec["automountServiceAccountToken"], False)
                for container in pod_spec["containers"]:
                    self.assertEqual(
                        container["resources"]["requests"],
                        container["resources"]["limits"],
                    )

        target = next(item for item in self.manifests if item["kind"] == "Pod")
        k6 = next(item for item in self.manifests if item["kind"] == "Job")
        self.assertEqual(
            target["spec"]["containers"][0]["resources"]["limits"],
            environment["resources"]["target"],
        )
        self.assertEqual(
            k6["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"],
            environment["resources"]["load_generator"],
        )

    def test_target_uses_business_ping_readiness_without_actuator(self):
        target = next(item for item in self.manifests if item["kind"] == "Pod")
        container = target["spec"]["containers"][0]

        self.assertEqual(container["ports"], [{"name": "http", "containerPort": 8080}])
        self.assertEqual(
            container["readinessProbe"]["httpGet"],
            {"path": "/ping", "port": "http"},
        )
        self.assertNotIn("actuator", self.rendered.lower())

    def test_workloads_are_non_privileged_and_hardened(self):
        workloads = [
            manifest
            for manifest in self.manifests
            if manifest["kind"] in {"Pod", "Job"}
        ]
        for workload in workloads:
            with self.subTest(kind=workload["kind"]):
                pod_spec = (
                    workload["spec"]
                    if workload["kind"] == "Pod"
                    else workload["spec"]["template"]["spec"]
                )
                self.assertIs(pod_spec["securityContext"]["runAsNonRoot"], True)
                for container in pod_spec["containers"]:
                    security = container["securityContext"]
                    self.assertIs(security["privileged"], False)
                    self.assertIs(security["allowPrivilegeEscalation"], False)
                    self.assertEqual(security["capabilities"]["drop"], ["ALL"])

    def test_target_runtime_image_declares_the_matching_non_root_user(self):
        dockerfile = DOCKERFILE.read_text()

        self.assertIn("useradd --system --uid 10001", dockerfile)
        self.assertIn("USER 10001", dockerfile)

    def test_transactional_manifest_contains_postgres_target_and_load_generator(self):
        manifests = list(yaml.safe_load_all(TRANSACTIONAL_MANIFEST.read_text()))

        self.assertEqual(
            [(item["kind"], item["metadata"]["name"]) for item in manifests],
            [
                ("Namespace", "__NAMESPACE__"),
                ("Service", "postgres"),
                ("Pod", "postgres"),
                ("Service", "target"),
                ("Pod", "target"),
                ("ConfigMap", "k6-script"),
                ("Job", "__K6_JOB_NAME__"),
            ],
        )

        postgres = next(
            item
            for item in manifests
            if item["kind"] == "Pod" and item["metadata"]["name"] == "postgres"
        )
        container = postgres["spec"]["containers"][0]
        self.assertEqual(
            container["image"],
            "postgres:18@sha256:"
            "0c49c0c906cb405ea65e70c284570fee91c7750ca9336369afc0edf4fce211db",
        )
        self.assertEqual(
            container["readinessProbe"]["exec"]["command"],
            ["pg_isready", "-U", "hrw", "-d", "hrw"],
        )
        self.assertEqual(
            postgres["spec"]["securityContext"],
            {
                "runAsNonRoot": True,
                "runAsUser": 999,
                "runAsGroup": 999,
                "fsGroup": 999,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        )
        self.assertEqual(
            container["resources"]["limits"], {"cpu": "1", "memory": "1Gi"}
        )

        target = next(
            item
            for item in manifests
            if item["kind"] == "Pod" and item["metadata"]["name"] == "target"
        )
        self.assertEqual(
            target["spec"]["containers"][0]["env"],
            "__TARGET_ENV__",
        )

        self._assert_scenario_workloads(manifests)

    def test_read_heavy_manifests_mount_the_postgres_init_sql(self):
        self.assertTrue(READ_HEAVY_COMPOSE.is_file())
        self.assertTrue(READ_HEAVY_MANIFEST.is_file())
        compose = yaml.safe_load(READ_HEAVY_COMPOSE.read_text())
        manifests = list(yaml.safe_load_all(READ_HEAVY_MANIFEST.read_text()))

        self.assertIn(
            "../scenarios/read-heavy-query-api/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro",
            compose["services"]["postgres"]["volumes"],
        )
        compose_healthcheck = compose["services"]["postgres"]["healthcheck"]["test"]
        self.assertEqual(compose_healthcheck[0], "CMD-SHELL")
        self.assertIn(
            "SELECT 1 FROM benchmark_init_markers WHERE name = 'catalog_products_ready'",
            compose_healthcheck[1],
        )
        self.assertNotIn("sum(price_cents)", compose_healthcheck[1])
        self.assertEqual(
            [(item["kind"], item["metadata"]["name"]) for item in manifests],
            [
                ("Namespace", "__NAMESPACE__"),
                ("ConfigMap", "postgres-init"),
                ("Service", "postgres"),
                ("Pod", "postgres"),
                ("Service", "target"),
                ("Pod", "target"),
                ("ConfigMap", "k6-script"),
                ("Job", "__K6_JOB_NAME__"),
            ],
        )

        init = next(
            item
            for item in manifests
            if item["kind"] == "ConfigMap"
            and item["metadata"]["name"] == "postgres-init"
        )
        self.assertEqual(init["data"], {"init.sql": "__POSTGRES_INIT_SQL__"})

        postgres = next(
            item
            for item in manifests
            if item["kind"] == "Pod" and item["metadata"]["name"] == "postgres"
        )
        container = postgres["spec"]["containers"][0]
        readiness = container["readinessProbe"]["exec"]["command"]
        self.assertEqual(readiness[:2], ["sh", "-ec"])
        self.assertIn(
            "SELECT 1 FROM benchmark_init_markers WHERE name = 'catalog_products_ready'",
            readiness[2],
        )
        self.assertNotIn("sum(price_cents)", readiness[2])
        init_sql = (
            ROOT / "scenarios/read-heavy-query-api/postgres/init.sql"
        ).read_text()
        self.assertIn("CREATE TABLE benchmark_init_markers", init_sql)
        self.assertIn("'catalog_products_ready'", init_sql)
        self.assertGreater(
            init_sql.index("INSERT INTO benchmark_init_markers"),
            init_sql.index("ANALYZE catalog_products"),
        )
        self.assertIn(
            {
                "name": "init",
                "mountPath": "/docker-entrypoint-initdb.d/init.sql",
                "subPath": "init.sql",
                "readOnly": True,
            },
            container["volumeMounts"],
        )
        self.assertIn(
            {"name": "init", "configMap": {"name": "postgres-init"}},
            postgres["spec"]["volumes"],
        )
        self._assert_scenario_workloads(manifests)

    def test_io_manifest_contains_wiremock_mappings_target_and_load_generator(self):
        manifests = list(yaml.safe_load_all(IO_AGGREGATION_MANIFEST.read_text()))

        self.assertEqual(
            [(item["kind"], item["metadata"]["name"]) for item in manifests],
            [
                ("Namespace", "__NAMESPACE__"),
                ("ConfigMap", "wiremock-mappings"),
                ("Service", "mock-upstream"),
                ("Pod", "mock-upstream"),
                ("Service", "target"),
                ("Pod", "target"),
                ("ConfigMap", "k6-script"),
                ("Job", "__K6_JOB_NAME__"),
            ],
        )

        mappings = next(
            item
            for item in manifests
            if item["kind"] == "ConfigMap"
            and item["metadata"]["name"] == "wiremock-mappings"
        )
        self.assertEqual(
            set(mappings["data"]),
            {"inventory.json", "profile.json", "recommendations.json"},
        )

        wiremock = next(
            item
            for item in manifests
            if item["kind"] == "Pod"
            and item["metadata"]["name"] == "mock-upstream"
        )
        container = wiremock["spec"]["containers"][0]
        self.assertEqual(
            container["image"],
            "wiremock/wiremock:3.13.2@sha256:"
            "d737d2de3664a7e1bf96f73a7bd48a0d47d61988f7ca88a6e51ea44b8c1f687d",
        )
        self.assertEqual(
            container["args"],
            [
                "--no-request-journal",
                "--container-threads",
                "128",
                "--jetty-accept-queue-size",
                "512",
            ],
        )
        self.assertEqual(
            container["readinessProbe"]["httpGet"],
            {"path": "/__admin/health", "port": "http"},
        )
        self.assertEqual(
            wiremock["spec"]["securityContext"],
            {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        )
        self.assertEqual(
            container["resources"]["limits"], {"cpu": "1", "memory": "1Gi"}
        )

        target = next(
            item
            for item in manifests
            if item["kind"] == "Pod" and item["metadata"]["name"] == "target"
        )
        self.assertEqual(
            target["spec"]["containers"][0]["env"],
            "__TARGET_ENV__",
        )

        self._assert_scenario_workloads(manifests)

    def _assert_scenario_workloads(self, manifests):
        environment = yaml.safe_load(ENVIRONMENT.read_text())
        for manifest in manifests:
            labels = manifest["metadata"]["labels"]
            self.assertEqual(
                labels["app.kubernetes.io/part-of"], "hello-realworld-bench"
            )
            self.assertEqual(labels["hello-real-world/run-set"], "__RUN_SET_ID__")

            if labels.get("app.kubernetes.io/component") == "dependency":
                self.assertEqual(labels["app.kubernetes.io/component"], "dependency")

            if manifest["kind"] not in {"Pod", "Job"}:
                continue

            pod_spec = (
                manifest["spec"]
                if manifest["kind"] == "Pod"
                else manifest["spec"]["template"]["spec"]
            )
            self.assertEqual(
                pod_spec["nodeSelector"],
                {"kubernetes.io/hostname": environment["cluster"]["node_name"]},
            )
            self.assertIs(pod_spec["automountServiceAccountToken"], False)
            self.assertIs(pod_spec["securityContext"]["runAsNonRoot"], True)
            self.assertEqual(
                pod_spec["securityContext"]["seccompProfile"],
                {"type": "RuntimeDefault"},
            )
            for container in pod_spec["containers"]:
                self.assertEqual(
                    container["resources"]["requests"],
                    container["resources"]["limits"],
                )
                security = container["securityContext"]
                self.assertIs(security["privileged"], False)
                self.assertIs(security["allowPrivilegeEscalation"], False)
                self.assertIs(security["readOnlyRootFilesystem"], True)
                self.assertEqual(security["capabilities"]["drop"], ["ALL"])

        dependency_pods = [
            item
            for item in manifests
            if item["kind"] == "Pod"
            and item["metadata"]["labels"].get("app.kubernetes.io/component")
            == "dependency"
        ]
        for dependency in dependency_pods:
            self.assertEqual(
                dependency["spec"]["containers"][0]["resources"]["limits"],
                environment["resources"]["dependency"],
            )
