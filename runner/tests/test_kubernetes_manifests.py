from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "infra" / "k8s" / "ping-api.yaml"
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
                "__JAVA_TOOL_OPTIONS__",
                "__K6_IMAGE__",
                "__K6_DURATION__",
                "__K6_VUS__",
                "__K6_JOB_NAME__",
                "__K6_SCRIPT__",
                "__VIRTUAL_THREADS__",
                "__KEEP_ALIVE__",
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
