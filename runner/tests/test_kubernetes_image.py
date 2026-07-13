import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hrw_runner.kubernetes_image import build_and_export_image, build_and_push_image


class KubernetesImageBuildTest(unittest.TestCase):
    @patch("hrw_runner.kubernetes_image.run")
    def test_can_export_an_oci_image_for_explicit_k3s_import(self, command):
        def write_metadata(arguments, **_kwargs):
            if arguments[:3] != ["docker", "buildx", "build"]:
                return
            metadata_path = Path(arguments[arguments.index("--metadata-file") + 1])
            metadata_path.write_text(
                json.dumps({"containerimage.digest": "sha256:" + "a" * 64})
            )

        command.side_effect = write_metadata
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "target.oci.tar"
            result = build_and_export_image(
                Path(temp_dir), "ghcr.io/example/target", "b" * 40, output
            )

        arguments = command.call_args_list[-1].args[0]
        self.assertIn(f"type=oci,dest={output}", arguments)
        self.assertIn("hello-realworld-oci", arguments)
        self.assertNotIn("--push", arguments)
        self.assertEqual(result["distribution"], "k3s-containerd-import")

    @patch("hrw_runner.kubernetes_image.run")
    def test_builds_linux_amd64_once_and_returns_immutable_reference(self, command):
        def write_metadata(arguments, **_kwargs):
            if arguments[:3] != ["docker", "buildx", "build"]:
                return
            metadata_path = Path(arguments[arguments.index("--metadata-file") + 1])
            metadata_path.write_text(
                json.dumps({"containerimage.digest": "sha256:" + "a" * 64})
            )

        command.side_effect = write_metadata
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_and_push_image(
                Path(temp_dir),
                "ghcr.io/example/target",
                "b" * 40,
            )

        self.assertEqual(command.call_count, 2)
        self.assertIn("./gradlew", command.call_args_list[0].args[0])
        arguments = command.call_args_list[1].args[0]
        self.assertEqual(arguments[:3], ["docker", "buildx", "build"])
        self.assertIn("linux/amd64", arguments)
        self.assertIn("--push", arguments)
        self.assertIn("ghcr.io/example/target:" + "b" * 40, arguments)
        self.assertEqual(
            result["image"],
            "ghcr.io/example/target@sha256:" + "a" * 64,
        )
        self.assertGreaterEqual(result["clean_build_ms"], 0)
        self.assertGreaterEqual(result["image_build_push_ms"], 0)

    @patch("hrw_runner.kubernetes_image.run")
    def test_rejects_missing_or_non_sha256_build_metadata(self, command):
        def write_metadata(arguments, **_kwargs):
            if arguments[:3] != ["docker", "buildx", "build"]:
                return
            metadata_path = Path(arguments[arguments.index("--metadata-file") + 1])
            metadata_path.write_text(json.dumps({"containerimage.digest": "latest"}))

        command.side_effect = write_metadata
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "immutable image digest"):
                build_and_push_image(Path(temp_dir), "ghcr.io/example/target", "b" * 40)


if __name__ == "__main__":
    unittest.main()
