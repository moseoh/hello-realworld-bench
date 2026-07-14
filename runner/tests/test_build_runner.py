from __future__ import annotations

import importlib
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from hrw_runner.build_config import resolve_build_run_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_GRADLE_IMAGE = (
    "eclipse-temurin:25-jdk@sha256:"
    "68868d04fa9cfd5f5c6abec0b5cef86d8de2bf9c62c37c7d3e4f0f80f5cfd7ff"
)
EXPECTED_BUILDKIT_IMAGE = (
    "moby/buildkit:buildx-stable-1@sha256:"
    "0168606be2315b7c807a03b3d8aa79beefdb31c98740cebdffdfeebf31190c9f"
)


def _runner_module():
    try:
        return importlib.import_module("hrw_runner.build_runner")
    except ModuleNotFoundError:
        raise AssertionError("hrw_runner.build_runner must exist") from None


class _FakeClock:
    def __init__(self):
        self.value = 1_000_000_000

    def monotonic_ns(self):
        value = self.value
        self.value += 10_000_000
        return value

    def utc_now(self):
        value = self.value
        self.value += 1_000_000
        return f"2026-07-14T00:00:{value % 60:02d}Z"


class _FakeCommandRunner:
    def __init__(self, root: Path, app_dir: Path):
        self.root = root
        self.app_dir = app_dir
        self.calls: list[tuple[list[str], Path | None, bool]] = []
        self.fail_first_timed_gradle = False

    def __call__(self, argv, *, cwd=None, check=True):
        argv = list(argv)
        self.calls.append((argv, cwd, check))
        stdout = ""
        if argv[:2] == ["git", "archive"]:
            output = Path(argv[argv.index("--output") + 1])
            with tarfile.open(output, "w") as archive:
                archive.add(
                    self.app_dir,
                    arcname=self.app_dir.relative_to(self.root).as_posix(),
                )
        elif argv[:2] == ["docker", "run"]:
            workspace = self._mount(argv, "/workspace")
            probe = workspace / "src/main/java/org/hellorealworld/benchmark/BuildBenchmarkProbe.java"
            value = b"0" if b"VALUE = 0" in probe.read_bytes() else b"1"
            artifact = workspace / "build/libs/application.jar"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"spring-app-" + value)
            stdout = "BUILD SUCCESSFUL\n"
            if self.fail_first_timed_gradle and "--offline" in argv and "clean" in argv:
                return subprocess.CompletedProcess(
                    argv, 1, stdout="BUILD FAILED\n", stderr=""
                )
        elif argv[:3] == ["docker", "buildx", "build"]:
            if "--target" in argv:
                cache_to = argv[argv.index("--cache-to") + 1]
                seed_dir = Path(cache_to.split("dest=", 1)[1].split(",", 1)[0])
                seed_dir.mkdir(parents=True)
                (seed_dir / "seed").write_text("runtime-base")
            else:
                output = argv[argv.index("--output") + 1]
                archive_path = Path(output.split("dest=", 1)[1].split(",", 1)[0])
                metadata_path = Path(argv[argv.index("--metadata-file") + 1])
                context = Path(argv[-1])
                artifact = next((context / "build/libs").glob("*.jar"))
                suffix = artifact.read_bytes()[-1:].decode()
                config_digest = ("c" if suffix == "0" else "d") * 64
                digest = self._write_oci_archive(archive_path, config_digest)
                metadata_path.write_text(
                    json.dumps({"containerimage.digest": f"sha256:{digest}"})
                )
        elif argv == ["docker", "version", "--format", "{{.Server.Version}}"]:
            stdout = "28.3.2\n"
        elif argv == ["docker", "buildx", "version"]:
            stdout = "github.com/docker/buildx v0.25.0\n"
        elif argv == ["docker", "ps", "--format", "{{.ID}} {{.Image}} {{.Names}}"]:
            stdout = ""
        elif argv == ["docker", "buildx", "ls", "--format", "{{.Name}}"]:
            stdout = "default\n"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    @staticmethod
    def _mount(argv: list[str], target: str) -> Path:
        for index, value in enumerate(argv):
            if value == "--mount":
                fields = dict(part.split("=", 1) for part in argv[index + 1].split(","))
                if fields.get("target") == target:
                    return Path(fields["src"])
        raise AssertionError(f"missing mount: {target}")

    @staticmethod
    def _write_oci_archive(path: Path, config_digest: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schemaVersion": 2,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": f"sha256:{config_digest}",
                "size": 1,
            },
            "layers": [],
        }
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode()
        manifest_digest = __import__("hashlib").sha256(manifest_bytes).hexdigest()
        index = {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{manifest_digest}",
                    "size": len(manifest_bytes),
                }
            ],
        }
        with tarfile.open(path, "w") as archive:
            for name, payload in (
                ("index.json", json.dumps(index).encode()),
                (f"blobs/sha256/{manifest_digest}", manifest_bytes),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                import io

                archive.addfile(info, io.BytesIO(payload))
        return manifest_digest


def _host_evidence():
    return {
        "machine_id": "f66cd2d134b94bb18eb7e531d1baf343",
        "cpu_model": "AMD Ryzen 7 5825U",
        "logical_cpu_count": 16,
        "memory_bytes": 32_000_000_000,
        "docker_version": "28.3.2",
        "buildx_version": "github.com/docker/buildx v0.25.0",
        "running_containers": [],
    }


class BuildRunnerTest(unittest.TestCase):
    def setUp(self):
        self.config = resolve_build_run_config(
            "java/spring-boot",
            "jvm-java25",
            PROJECT_ROOT,
            environment_profile="home-build-v1",
            measurement_protocol="official-build-v1",
            build_profile="official-gradle-docker-v1",
        )

    def test_pinned_images_limits_and_variant_owned_application_artifacts(self):
        module = _runner_module()

        self.assertEqual(module.GRADLE_EXECUTOR_IMAGE, EXPECTED_GRADLE_IMAGE)
        self.assertEqual(module.BUILDKIT_IMAGE, EXPECTED_BUILDKIT_IMAGE)
        self.assertEqual(
            self.config.build["incremental_input"]["path"],
            "src/main/java/org/hellorealworld/benchmark/BuildBenchmarkProbe.java",
        )
        self.assertEqual(self.config.build["application_artifact"], {
            "type": "glob",
            "path": "build/libs/*.jar",
        })
        quarkus = resolve_build_run_config(
            "java/quarkus",
            "jvm-java25",
            PROJECT_ROOT,
            environment_profile="home-build-v1",
            measurement_protocol="official-build-v1",
            build_profile="official-gradle-docker-v1",
        )
        self.assertEqual(quarkus.build["application_artifact"], {
            "type": "directory",
            "path": "build/quarkus-app",
        })

    def test_probe_sources_must_match_and_contain_exact_from_text_before_commands(self):
        module = _runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for implementation in ("spring-boot", "quarkus"):
                path = root / "implementations/java" / implementation / self.config.build["incremental_input"]["path"]
                path.parent.mkdir(parents=True)
                path.write_text("public static final int VALUE = 0;\n")
                paths.append(path)
            module.validate_probe_sources(root, self.config.build["incremental_input"])

            paths[1].write_text("package changed;\npublic static final int VALUE = 0;\n")
            with self.assertRaisesRegex(ValueError, "byte-identical"):
                module.validate_probe_sources(root, self.config.build["incremental_input"])

    def test_deterministic_source_mutation_requires_exactly_one_match(self):
        module = _runner_module()
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "BuildBenchmarkProbe.java"
            probe.write_text("public static final int VALUE = 0;\n")
            before, after = module.mutate_probe(
                probe,
                "public static final int VALUE = 0;",
                "public static final int VALUE = 1;",
            )
            self.assertNotEqual(before, after)
            self.assertIn("VALUE = 1", probe.read_text())
            with self.assertRaisesRegex(ValueError, "exactly once"):
                module.mutate_probe(probe, "VALUE = 0", "VALUE = 1")

    def test_run_uses_fresh_trial_inputs_exact_operation_order_and_command_boundaries(self):
        module = _runner_module()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            seed = temp / "dependency-seed"
            seed.mkdir()
            (seed / "modules.bin").write_bytes(b"immutable")
            fake = _FakeCommandRunner(PROJECT_ROOT, self.config.app_dir)
            clock = _FakeClock()
            output = module.run_build_benchmark_set(
                self.config,
                command_runner=fake,
                dependency_seed=seed,
                results_root=temp / "results",
                host_probe=lambda: _host_evidence(),
                monotonic_ns=clock.monotonic_ns,
                utc_now=clock.utc_now,
            )

            run_set = json.loads((output / "build-run-set.json").read_text())
            self.assertEqual(len(run_set["trials"]), 3)
            workspaces = set()
            caches = set()
            for index in range(1, 4):
                trial_dir = output / "trials" / f"{index:02d}"
                trial = json.loads((trial_dir / "build-trial.json").read_text())
                self.assertEqual(
                    [operation["name"] for operation in trial["operations"]],
                    [
                        "gradle_clean_build",
                        "image_package",
                        "gradle_incremental_rebuild",
                        "image_rebuild",
                    ],
                )
                for operation in trial["operations"]:
                    raw = json.loads((trial_dir / operation["path"]).read_text())
                    self.assertEqual(raw["duration_ms"], 10.0)
                    self.assertEqual(
                        raw["duration_ms"],
                        (raw["end_monotonic_ns"] - raw["start_monotonic_ns"]) / 1_000_000,
                    )
                setup = json.loads((trial_dir / "trial-inputs.json").read_text())
                workspaces.add(setup["workspace"])
                caches.add(setup["dependency_cache"])
                self.assertEqual(setup["dependency_seed_sha256"], setup["dependency_cache_initial_sha256"])
                artifacts = json.loads((trial_dir / "application-artifacts.json").read_text())
                self.assertNotEqual(artifacts["before"]["sha256"], artifacts["after"]["sha256"])
                images = json.loads((trial_dir / "image-artifacts.json").read_text())
                self.assertNotEqual(images["before"]["image_digest"], images["after"]["image_digest"])
                self.assertFalse((trial_dir / "image-package.oci").exists())
                self.assertFalse((trial_dir / "image-rebuild.oci").exists())
            self.assertEqual(len(workspaces), 3)
            self.assertEqual(len(caches), 3)

            calls = [argv for argv, _, _ in fake.calls]
            self.assertLess(calls.index(["docker", "pull", EXPECTED_GRADLE_IMAGE]), next(i for i, call in enumerate(calls) if call[:2] == ["docker", "run"]))
            self.assertLess(calls.index(["docker", "pull", EXPECTED_BUILDKIT_IMAGE]), next(i for i, call in enumerate(calls) if call[:3] == ["docker", "buildx", "build"]))
            gradle_calls = [call for call in calls if call[:2] == ["docker", "run"]]
            self.assertEqual(len(gradle_calls), 6)
            for call in gradle_calls:
                self.assertIn("--cpus", call)
                self.assertEqual(call[call.index("--cpus") + 1], "2")
                self.assertEqual(call[call.index("--memory") + 1], "4g")
                self.assertEqual(call[call.index("--memory-swap") + 1], "4g")
                self.assertIn(EXPECTED_GRADLE_IMAGE, call)
                self.assertEqual(
                    call[call.index("--user") + 1], f"{os.getuid()}:{os.getgid()}"
                )
            create_calls = [call for call in calls if call[:3] == ["docker", "buildx", "create"]]
            self.assertEqual(len(create_calls), 4)
            for call in create_calls:
                driver_opt = call[call.index("--driver-opt") + 1]
                self.assertEqual(
                    driver_opt,
                    f"image={EXPECTED_BUILDKIT_IMAGE},cpu-quota=200000,cpu-period=100000,memory=4g,memory-swap=4g",
                )
            image_calls = [call for call in calls if call[:3] == ["docker", "buildx", "build"] and "--target" not in call]
            self.assertEqual(len(image_calls), 6)
            for position, call in enumerate(image_calls):
                self.assertIn("--platform", call)
                self.assertEqual(call[call.index("--platform") + 1], "linux/amd64")
                self.assertIn("--provenance=false", call)
                self.assertIn("--metadata-file", call)
                self.assertIn("type=oci,dest=", call[call.index("--output") + 1])
                if position % 2 == 0:
                    self.assertIn("--cache-from", call)
                else:
                    self.assertNotIn("--cache-from", call)

    def test_prepares_and_freezes_dependency_seed_before_timed_offline_operations(self):
        module = _runner_module()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            fake = _FakeCommandRunner(PROJECT_ROOT, self.config.app_dir)
            clock = _FakeClock()
            output = module.run_build_benchmark_set(
                self.config,
                command_runner=fake,
                results_root=temp / "results",
                host_probe=lambda: _host_evidence(),
                monotonic_ns=clock.monotonic_ns,
                utc_now=clock.utc_now,
            )

            docker_runs = [argv for argv, _, _ in fake.calls if argv[:2] == ["docker", "run"]]
            self.assertEqual(len(docker_runs), 7)
            seed_call = docker_runs[0]
            self.assertNotIn("--offline", seed_call)
            self.assertIn("clean", seed_call)
            self.assertIn("build", seed_call)
            self.assertEqual(
                seed_call[seed_call.index("--user") + 1],
                f"{os.getuid()}:{os.getgid()}",
            )
            for timed_call in docker_runs[1:]:
                self.assertIn("--offline", timed_call)
                self.assertIn("--no-daemon", timed_call)
                self.assertIn("--no-build-cache", timed_call)

            cache_seed = json.loads((output / "cache-seed.json").read_text())
            self.assertEqual(cache_seed["dependency_seed_mode"], "prepared")
            self.assertTrue(cache_seed["workspace_build_outputs_removed"])
            self.assertTrue(cache_seed["gradle_runtime_state_removed"])
            first_operation = json.loads(
                (output / "trials/01/operations/01-gradle_clean_build.json").read_text()
            )
            self.assertGreater(first_operation["start_monotonic_ns"], 1_000_000_000)

    def test_seed_cleanup_preserves_dependency_directories_named_build(self):
        module = _runner_module()
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory)
            dependency_build = seed / "caches/modules-2/files-2.1/example/build"
            dependency_build.mkdir(parents=True)
            (dependency_build / "artifact.bin").write_bytes(b"keep")
            for state in (
                seed / "caches/build-cache-1",
                seed / "daemon",
                seed / "workers",
            ):
                state.mkdir(parents=True)
                (state / "state.bin").write_bytes(b"remove")

            module.clean_gradle_seed_state(seed)

            self.assertTrue((dependency_build / "artifact.bin").is_file())
            self.assertFalse((seed / "caches/build-cache-1").exists())
            self.assertFalse((seed / "daemon").exists())
            self.assertFalse((seed / "workers").exists())

    def test_failure_preserves_logs_and_writes_failure_evidence(self):
        module = _runner_module()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            seed = temp / "dependency-seed"
            seed.mkdir()
            fake = _FakeCommandRunner(PROJECT_ROOT, self.config.app_dir)
            fake.fail_first_timed_gradle = True
            with self.assertRaisesRegex(RuntimeError, "gradle_clean_build"):
                module.run_build_benchmark_set(
                    self.config,
                    command_runner=fake,
                    dependency_seed=seed,
                    results_root=temp / "results",
                    host_probe=lambda: _host_evidence(),
                )

            outputs = list((temp / "results").iterdir())
            self.assertEqual(len(outputs), 1)
            failure = json.loads((outputs[0] / "failure.json").read_text())
            self.assertEqual(failure["failure_type"], "RuntimeError")
            self.assertIn("gradle_clean_build", failure["message"])
            self.assertEqual(
                (outputs[0] / "trials/01/operations/01-gradle_clean_build.log").read_text(),
                "BUILD FAILED\n",
            )

    def test_preflight_rejects_benchmark_builder_or_state_residue_before_pull(self):
        module = _runner_module()
        for field, value in (
            ("builders", ["hrw-build-stale"]),
            ("buildkit_state_volumes", ["buildx_buildkit_hrw-build-stale0_state"]),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                host = _host_evidence()
                host[field] = value
                fake = _FakeCommandRunner(PROJECT_ROOT, self.config.app_dir)
                with self.assertRaises(ValueError):
                    module.run_build_benchmark_set(
                        self.config,
                        command_runner=fake,
                        dependency_seed=Path(directory),
                        results_root=Path(directory) / "results",
                        host_probe=lambda: host,
                    )
                self.assertNotIn(
                    ["docker", "pull", EXPECTED_GRADLE_IMAGE],
                    [argv for argv, _, _ in fake.calls],
                )

    def test_builder_resources_are_campaign_unique_and_cleanup_is_exact(self):
        module = _runner_module()
        first = module._builder_resources(
            "2026-07-14T00-00-00_java_spring-boot_jvm-java25_build", 1
        )
        second = module._builder_resources(
            "2026-07-14T00-00-01_java_spring-boot_jvm-java25_build", 1
        )

        self.assertNotEqual(first["trial_builder"], second["trial_builder"])
        self.assertNotEqual(first["trial_state_volume"], second["trial_state_volume"])
        self.assertNotEqual(first["seed_builder"], second["seed_builder"])
        self.assertNotEqual(first["seed_state_volume"], second["seed_state_volume"])

        fake = _FakeCommandRunner(PROJECT_ROOT, self.config.app_dir)
        module._remove_builder(
            first["trial_builder"], first["trial_state_volume"], fake
        )
        calls = [argv for argv, _, _ in fake.calls]
        self.assertIn(
            ["docker", "buildx", "rm", "--force", first["trial_builder"]], calls
        )
        self.assertIn(
            [
                "docker",
                "volume",
                "rm",
                "--force",
                first["trial_state_volume"],
            ],
            calls,
        )
        self.assertFalse(
            any(second["trial_builder"] in argument for call in calls for argument in call)
        )


if __name__ == "__main__":
    unittest.main()
