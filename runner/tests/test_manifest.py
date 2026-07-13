import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from hrw_runner.config import resolve_run_config
from hrw_runner.contracts import ContractDocument
from hrw_runner.manifest import (
    ManifestValidationError,
    build_resolved_manifest,
    read_git_provenance,
    resolve_input_assets,
    validate_resolved_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = {
    "git_commit": "a" * 40,
    "git_dirty": False,
    "worktree_digest": "b" * 64,
}


def _digest(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _rehash_manifest(manifest):
    cohort_payload = {
        key: value
        for key, value in manifest["cohort"].items()
        if key != "fingerprint"
    }
    manifest["cohort"]["fingerprint"] = _digest(cohort_payload)
    manifest_payload = {
        key: value for key, value in manifest.items() if key != "manifest_digest"
    }
    manifest["manifest_digest"] = _digest(manifest_payload)


class ResolvedManifestTest(unittest.TestCase):
    def test_read_heavy_manifest_records_arrival_rate_calibration_state(self):
        config = resolve_run_config(
            "java/spring-boot",
            "read-heavy-query-api",
            "jvm-java25",
            PROJECT_ROOT,
        )

        manifest = build_resolved_manifest(config, "run-001", self.source)

        self.assertIs(manifest["execution"]["load"]["arrival_rate"]["calibrated"], True)
        validate_resolved_manifest(manifest, PROJECT_ROOT)

    def test_k3s_manifest_uses_the_scenario_kubernetes_template_without_compose(self):
        config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            "jvm-java25",
            PROJECT_ROOT,
            environment_profile="home-k3s-v1",
            measurement_protocol="official-service-v1",
            load_profile="platform-qualification-v1",
        )

        assets = resolve_input_assets(config)
        roles = [asset["role"] for asset in assets]

        self.assertIn("kubernetes-template", roles)
        self.assertNotIn("environment-compose", roles)
        self.assertNotIn("implementation-compose", roles)
        self.assertIn(
            "infra/k8s/ping-api.yaml",
            [asset["path"] for asset in assets],
        )

    def test_k3s_manifest_accepts_only_the_official_immutable_image_repository(self):
        config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            "jvm-java25",
            PROJECT_ROOT,
            environment_profile="home-k3s-v1",
            measurement_protocol="official-service-v1",
            load_profile="platform-qualification-v1",
        )
        immutable = replace(
            config,
            image_tag=(
                "ghcr.io/moseoh/hello-realworld-bench/spring-boot@sha256:"
                + "c" * 64
            ),
        )
        manifest = build_resolved_manifest(immutable, "run-id", read_git_provenance(PROJECT_ROOT))

        validate_resolved_manifest(manifest, PROJECT_ROOT)
        manifest["execution"]["image_tag"] = "ghcr.io/other/target@sha256:" + "c" * 64
        manifest_payload = {
            key: value for key, value in manifest.items() if key != "manifest_digest"
        }
        manifest["manifest_digest"] = hashlib.sha256(
            json.dumps(
                manifest_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()

        with self.assertRaisesRegex(ManifestValidationError, "official target repository"):
            validate_resolved_manifest(manifest, PROJECT_ROOT)

    def setUp(self):
        self.config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            "jvm-java25",
            PROJECT_ROOT,
        )
        self.source = read_git_provenance(PROJECT_ROOT)

    def test_builds_deterministic_manifest_with_selected_contracts_and_assets(self):
        manifest = build_resolved_manifest(self.config, "run-001", self.source)
        reordered = replace(
            self.config,
            selected_contracts=dict(
                reversed(list(self.config.selected_contracts.items()))
            ),
            runtime=dict(reversed(list(self.config.runtime.items()))),
        )
        second = build_resolved_manifest(
            reordered,
            "run-001",
            dict(reversed(list(self.source.items()))),
        )

        self.assertEqual(
            json.dumps(manifest, separators=(",", ":")),
            json.dumps(second, separators=(",", ":")),
        )
        self.assertEqual(manifest["schema_version"], "1.0")
        self.assertEqual(
            set(manifest["contracts"]),
            {
                "implementation",
                "variant",
                "scenario",
                "environment_profile",
                "measurement_protocol",
                "load_profile",
                "build_profile",
            },
        )
        for role, selected in self.config.selected_contracts.items():
            with self.subTest(contract=role):
                contract_ref = manifest["contracts"][role]
                self.assertEqual(
                    contract_ref["contract_version"],
                    selected.value["contract_version"],
                )
                self.assertEqual(contract_ref["digest"], selected.digest)
                self.assertEqual(
                    contract_ref["path"],
                    selected.path.relative_to(PROJECT_ROOT).as_posix(),
                )
        self.assertEqual(
            manifest["execution"]["runtime"]["language"],
            self.config.language,
        )
        self.assertEqual(
            manifest["execution"]["runtime"]["framework"],
            self.config.framework,
        )
        assets = {(asset["role"], asset["path"]): asset for asset in manifest["assets"]}
        self.assertIn(
            ("environment-compose", "infra/docker-compose.base.yml"),
            assets,
        )
        self.assertIn(
            ("implementation-compose", "infra/docker-compose.spring-boot.yml"),
            assets,
        )
        self.assertIn(("scenario-file", "scenarios/ping-api/k6.js"), assets)
        self.assertRegex(assets[("scenario-file", "scenarios/ping-api/k6.js")]["sha256"], r"^[0-9a-f]{64}$")
        validate_resolved_manifest(manifest, PROJECT_ROOT)

    def test_non_comparable_changes_only_change_manifest_digest(self):
        baseline = build_resolved_manifest(self.config, "run-001", SOURCE)
        changes = [
            replace(self.config, implementation="java/other"),
            resolve_run_config(
                "java/spring-boot",
                "ping-api",
                "jvm-java25-virtual-threads",
                PROJECT_ROOT,
            ),
            self._replace_contract("build_profile", "c" * 64),
        ]

        for changed_config in changes:
            with self.subTest(selection=changed_config.variant, implementation=changed_config.implementation):
                changed = build_resolved_manifest(changed_config, "run-001", SOURCE)
                self.assertNotEqual(changed["manifest_digest"], baseline["manifest_digest"])
                self.assertEqual(changed["cohort"]["fingerprint"], baseline["cohort"]["fingerprint"])

        for run_id, source in (
            ("run-002", SOURCE),
            ("run-001", {**SOURCE, "git_dirty": True}),
        ):
            changed = build_resolved_manifest(self.config, run_id, source)
            self.assertNotEqual(changed["manifest_digest"], baseline["manifest_digest"])
            self.assertEqual(changed["cohort"]["fingerprint"], baseline["cohort"]["fingerprint"])

    def test_comparable_contract_changes_change_cohort_fingerprint(self):
        baseline = build_resolved_manifest(self.config, "run-001", SOURCE)

        for index, role in enumerate(
            ("scenario", "load_profile", "environment_profile", "measurement_protocol"),
            start=1,
        ):
            changed_config = self._replace_contract(role, f"{index:x}" * 64)
            changed = build_resolved_manifest(changed_config, "run-001", SOURCE)
            self.assertNotEqual(
                changed["cohort"]["fingerprint"],
                baseline["cohort"]["fingerprint"],
            )

    def test_comparable_asset_change_changes_cohort_fingerprint(self):
        root = self._copy_runnable_repository()
        config = resolve_run_config("java/spring-boot", "ping-api", None, root)
        baseline = build_resolved_manifest(config, "run-001", SOURCE)

        (root / "scenarios/ping-api/k6.js").write_text("export default function () {}\n")
        changed = build_resolved_manifest(config, "run-001", SOURCE)

        self.assertNotEqual(
            changed["cohort"]["fingerprint"],
            baseline["cohort"]["fingerprint"],
        )

    def test_nested_scenario_document_name_is_a_comparable_machine_asset(self):
        root = self._copy_runnable_repository()
        fixture_dir = root / "scenarios/ping-api/fixtures"
        fixture_dir.mkdir()
        fixture = fixture_dir / "scenario.yaml"
        fixture.write_text("value: first\n")
        config = resolve_run_config("java/spring-boot", "ping-api", None, root)
        baseline = build_resolved_manifest(config, "run-001", SOURCE)

        fixture.write_text("value: second\n")
        changed = build_resolved_manifest(config, "run-001", SOURCE)

        self.assertIn(
            "scenarios/ping-api/fixtures/scenario.yaml",
            [asset["path"] for asset in baseline["assets"]],
        )
        self.assertNotEqual(
            changed["cohort"]["fingerprint"],
            baseline["cohort"]["fingerprint"],
        )

    def test_validation_rejects_tampered_hashes_and_cohort_projection(self):
        manifest = build_resolved_manifest(self.config, "run-001", self.source)
        cases = []

        tampered_manifest = copy.deepcopy(manifest)
        tampered_manifest["manifest_digest"] = "0" * 64
        cases.append((tampered_manifest, "$.manifest_digest"))

        tampered_cohort = copy.deepcopy(manifest)
        tampered_cohort["cohort"]["fingerprint"] = "0" * 64
        tampered_cohort["manifest_digest"] = _digest(
            {key: value for key, value in tampered_cohort.items() if key != "manifest_digest"}
        )
        cases.append((tampered_cohort, "$.cohort.fingerprint"))

        inconsistent = copy.deepcopy(manifest)
        inconsistent["cohort"]["contracts"]["scenario"] = copy.deepcopy(
            inconsistent["contracts"]["load_profile"]
        )
        cohort_payload = {
            key: value
            for key, value in inconsistent["cohort"].items()
            if key != "fingerprint"
        }
        inconsistent["cohort"]["fingerprint"] = _digest(cohort_payload)
        inconsistent["manifest_digest"] = _digest(
            {key: value for key, value in inconsistent.items() if key != "manifest_digest"}
        )
        cases.append((inconsistent, "$.cohort.contracts.scenario"))

        for value, location in cases:
            with self.subTest(location=location):
                with self.assertRaises(ManifestValidationError) as context:
                    validate_resolved_manifest(value, PROJECT_ROOT)
                self.assertIn(location, str(context.exception))

    def test_validation_rejects_recomputed_repository_bound_tampering(self):
        manifest = build_resolved_manifest(self.config, "run-001", self.source)
        cases = (
            (
                "selection",
                lambda value: value["selection"].__setitem__(
                    "variant", "jvm-java25-virtual-threads"
                ),
                "$.contracts.variant",
            ),
            (
                "contract path",
                lambda value: value["contracts"]["implementation"].__setitem__(
                    "path",
                    "implementations/java/spring-boot/variants/jvm-java25.yaml",
                ),
                "$.contracts.implementation.path",
            ),
            (
                "contract version",
                lambda value: value["contracts"]["implementation"].__setitem__(
                    "contract_version", "9.9"
                ),
                "$.contracts.implementation.contract_version",
            ),
            (
                "contract digest",
                lambda value: value["contracts"]["implementation"].__setitem__(
                    "digest", "0" * 64
                ),
                "$.contracts.implementation.digest",
            ),
            (
                "execution",
                lambda value: value["execution"].__setitem__(
                    "image_tag", "tampered:latest"
                ),
                "$.execution.image_tag",
            ),
            (
                "asset",
                lambda value: value["assets"][1].__setitem__(
                    "sha256", "0" * 64
                ),
                "$.assets[1].sha256",
            ),
            (
                "source",
                lambda value: value["source"].__setitem__(
                    "git_dirty", not self.source["git_dirty"]
                ),
                "$.source.git_dirty",
            ),
        )

        for name, mutate, location in cases:
            with self.subTest(tamper=name):
                tampered = copy.deepcopy(manifest)
                mutate(tampered)
                _rehash_manifest(tampered)
                with self.assertRaises(ManifestValidationError) as context:
                    validate_resolved_manifest(tampered, PROJECT_ROOT)
                self.assertIn(location, str(context.exception))

    def test_validation_accepts_a_fully_resolved_different_valid_selection(self):
        config = resolve_run_config(
            "java/spring-boot",
            "ping-api",
            "jvm-java25-virtual-threads",
            PROJECT_ROOT,
        )
        manifest = build_resolved_manifest(config, "run-001", self.source)

        validate_resolved_manifest(manifest, PROJECT_ROOT)

    def test_validation_rejects_non_absolute_end_values(self):
        manifest = build_resolved_manifest(self.config, "run-001", self.source)
        cases = (
            (
                "digest",
                lambda value: value["contracts"]["implementation"].__setitem__(
                    "digest", value["contracts"]["implementation"]["digest"] + "\n"
                ),
                "$.contracts.implementation.digest",
            ),
            (
                "git commit",
                lambda value: value["source"].__setitem__(
                    "git_commit", value["source"]["git_commit"] + "\n"
                ),
                "$.source.git_commit",
            ),
            (
                "run id",
                lambda value: value.__setitem__("run_id", value["run_id"] + "\n"),
                "$.run_id",
            ),
            (
                "path",
                lambda value: value["contracts"]["implementation"].__setitem__(
                    "path", value["contracts"]["implementation"]["path"] + "\n"
                ),
                "$.contracts.implementation.path",
            ),
        )

        for name, mutate, location in cases:
            with self.subTest(value=name):
                tampered = copy.deepcopy(manifest)
                mutate(tampered)
                _rehash_manifest(tampered)
                with self.assertRaises(ManifestValidationError) as context:
                    validate_resolved_manifest(tampered, PROJECT_ROOT)
                self.assertIn(location, str(context.exception))

    def test_validation_rejects_non_canonical_repository_paths(self):
        manifest = build_resolved_manifest(self.config, "run-001", self.source)

        for path in (
            "/infra/file",
            "infra\\file",
            "infra//file",
            "infra/./file",
            "infra/../file",
            "infra/file/",
            "infra/fi\x00le",
            "infra/e\u0301",
        ):
            with self.subTest(path=repr(path)):
                tampered = copy.deepcopy(manifest)
                tampered["contracts"]["implementation"]["path"] = path
                _rehash_manifest(tampered)
                with self.assertRaises(ManifestValidationError) as context:
                    validate_resolved_manifest(tampered, PROJECT_ROOT)
                self.assertIn("$.contracts.implementation.path", str(context.exception))

    def test_resolves_optional_compose_and_recursive_scenario_assets(self):
        config = resolve_run_config(
            "java/spring-boot",
            "io-aggregation-api",
            "jvm-java25-virtual-threads",
            PROJECT_ROOT,
        )

        assets = resolve_input_assets(config)
        roles_and_paths = [(asset["role"], asset["path"]) for asset in assets]

        self.assertEqual(
            roles_and_paths,
            [
                ("environment-compose", "infra/docker-compose.base.yml"),
                (
                    "implementation-compose",
                    "infra/docker-compose.spring-boot.yml",
                ),
                (
                    "variant-compose",
                    "infra/docker-compose.spring-boot.jvm-java25-virtual-threads.yml",
                ),
                (
                    "scenario-compose",
                    "infra/docker-compose.io-aggregation-api.yml",
                ),
                ("scenario-file", "scenarios/io-aggregation-api/k6.js"),
                (
                    "scenario-file",
                    "scenarios/io-aggregation-api/wiremock/mappings/inventory.json",
                ),
                (
                    "scenario-file",
                    "scenarios/io-aggregation-api/wiremock/mappings/profile.json",
                ),
                (
                    "scenario-file",
                    "scenarios/io-aggregation-api/wiremock/mappings/recommendations.json",
                ),
            ],
        )
        self.assertNotIn(
            ("scenario-file", "scenarios/io-aggregation-api/README.md"),
            roles_and_paths,
        )
        self.assertNotIn(
            ("scenario-file", "scenarios/io-aggregation-api/scenario.yaml"),
            roles_and_paths,
        )

    def test_includes_hidden_scenario_machine_files(self):
        root = self._copy_runnable_repository()
        scenario_dir = root / "scenarios/ping-api"
        (scenario_dir / ".env").write_text("KEY=value\n")
        (scenario_dir / "nested").mkdir()
        (scenario_dir / "nested/.fixture.json").write_text("{}\n")
        config = resolve_run_config("java/spring-boot", "ping-api", None, root)

        paths = [asset["path"] for asset in resolve_input_assets(config)]

        self.assertIn("scenarios/ping-api/.env", paths)
        self.assertIn("scenarios/ping-api/nested/.fixture.json", paths)

    def test_rejects_internal_and_external_scenario_symlinks(self):
        cases = ("internal-file", "internal-directory", "external-file", "external-directory")
        for case in cases:
            with self.subTest(case=case):
                root = self._copy_runnable_repository()
                scenario_dir = root / "scenarios/ping-api"
                link = scenario_dir / f"{case}-link"
                if case == "internal-file":
                    target = scenario_dir / "target.json"
                    target.write_text("{}\n")
                elif case == "internal-directory":
                    target = scenario_dir / "target-directory"
                    target.mkdir()
                    (target / "data.json").write_text("{}\n")
                elif case == "external-file":
                    target = root.parent / f"{root.name}-outside.json"
                    target.write_text("{}\n")
                    self.addCleanup(target.unlink)
                else:
                    target = root.parent / f"{root.name}-outside-directory"
                    target.mkdir()
                    self.addCleanup(target.rmdir)
                os.symlink(target, link)
                config = resolve_run_config("java/spring-boot", "ping-api", None, root)

                with self.assertRaisesRegex(
                    ManifestValidationError,
                    f"scenarios/ping-api/{case}-link",
                ):
                    resolve_input_assets(config)

    def test_validation_rejects_reordered_and_duplicate_assets(self):
        config = resolve_run_config(
            "java/spring-boot",
            "io-aggregation-api",
            "jvm-java25-virtual-threads",
            PROJECT_ROOT,
        )
        manifest = build_resolved_manifest(config, "run-001", self.source)

        reordered = copy.deepcopy(manifest)
        reordered["assets"][1], reordered["assets"][2] = (
            reordered["assets"][2],
            reordered["assets"][1],
        )
        _rehash_manifest(reordered)
        with self.assertRaises(ManifestValidationError) as context:
            validate_resolved_manifest(reordered, PROJECT_ROOT)
        self.assertIn("$.assets[1]", str(context.exception))

        duplicate = copy.deepcopy(manifest)
        duplicate["assets"].append(copy.deepcopy(duplicate["assets"][0]))
        _rehash_manifest(duplicate)
        with self.assertRaises(ManifestValidationError) as context:
            validate_resolved_manifest(duplicate, PROJECT_ROOT)
        self.assertIn("$.assets", str(context.exception))

    def test_rejects_internal_compose_symlink(self):
        root = self._copy_runnable_repository()
        config = resolve_run_config("java/spring-boot", "ping-api", None, root)
        compose = root / "infra/docker-compose.base.yml"
        target = root / "infra/docker-compose.base-real.yml"
        compose.rename(target)
        os.symlink(target.name, compose)

        with self.assertRaisesRegex(
            ManifestValidationError,
            "infra/docker-compose.base.yml",
        ):
            resolve_input_assets(config)

    def test_rejects_selected_contract_file_and_parent_symlinks(self):
        cases = ("file", "parent")
        for case in cases:
            with self.subTest(case=case):
                root = self._copy_runnable_repository()
                config = resolve_run_config("java/spring-boot", "ping-api", None, root)
                role = "load_profile"
                document = config.selected_contracts[role]
                if case == "file":
                    target = document.path.with_name("load-profile-real.yaml")
                    shutil.copy2(document.path, target)
                    contract_path = document.path.with_name("load-profile-link.yaml")
                    os.symlink(target.name, contract_path)
                else:
                    alias = root / "contracts/load-profiles-link"
                    os.symlink(root / "contracts/load-profiles", alias)
                    contract_path = alias / document.path.name
                selected = dict(config.selected_contracts)
                selected[role] = replace(document, path=contract_path)
                config = replace(config, selected_contracts=selected)

                with self.assertRaisesRegex(
                    ManifestValidationError,
                    contract_path.relative_to(root).as_posix(),
                ):
                    build_resolved_manifest(config, "run-001", SOURCE)

    def test_requires_base_and_implementation_compose_assets(self):
        root = self._copy_runnable_repository()
        config = resolve_run_config("java/spring-boot", "ping-api", None, root)

        for relative_path in (
            "infra/docker-compose.base.yml",
            "infra/docker-compose.spring-boot.yml",
        ):
            with self.subTest(path=relative_path):
                path = root / relative_path
                content = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaisesRegex(
                        ManifestValidationError,
                        relative_path,
                    ):
                        resolve_input_assets(config)
                finally:
                    path.write_bytes(content)

    def _replace_contract(self, role: str, digest: str):
        selected = dict(self.config.selected_contracts)
        document = selected[role]
        selected[role] = ContractDocument(
            document.kind,
            document.path,
            document.value,
            digest,
        )
        return replace(self.config, selected_contracts=selected)

    def _copy_runnable_repository(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        shutil.copytree(PROJECT_ROOT / "contracts", root / "contracts")
        app_dir = root / "implementations/java/spring-boot"
        app_dir.mkdir(parents=True)
        shutil.copy2(
            PROJECT_ROOT / "implementations/java/spring-boot/implementation.yaml",
            app_dir / "implementation.yaml",
        )
        shutil.copytree(
            PROJECT_ROOT / "implementations/java/spring-boot/variants",
            app_dir / "variants",
        )
        shutil.copytree(PROJECT_ROOT / "scenarios", root / "scenarios")
        shutil.copytree(PROJECT_ROOT / "infra", root / "infra")
        return root


class GitProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self._git("init", "-q")
        self._git("config", "user.name", "Manifest Test")
        self._git("config", "user.email", "manifest@example.com")
        (self.root / ".gitignore").write_text("ignored/\n")
        (self.root / "tracked.txt").write_text("tracked\n")
        (self.root / "executable.sh").write_text("#!/bin/sh\n")
        self._git("add", ".")
        self._git("commit", "-qm", "initial")

    def test_tracks_clean_dirty_and_worktree_content_markers(self):
        clean = read_git_provenance(self.root)
        self.assertFalse(clean["git_dirty"])
        self.assertRegex(clean["git_commit"], r"^[0-9a-f]{40}$")

        ignored = self.root / "ignored/cache.bin"
        ignored.parent.mkdir()
        ignored.write_bytes(b"ignored")
        self.assertEqual(read_git_provenance(self.root), clean)

        untracked = self.root / "untracked.txt"
        untracked.write_text("first\n")
        first_untracked = read_git_provenance(self.root)
        self.assertTrue(first_untracked["git_dirty"])
        self.assertNotEqual(first_untracked["worktree_digest"], clean["worktree_digest"])
        untracked.write_text("second\n")
        self.assertNotEqual(
            read_git_provenance(self.root)["worktree_digest"],
            first_untracked["worktree_digest"],
        )
        untracked.unlink()

        (self.root / "tracked.txt").unlink()
        deleted = read_git_provenance(self.root)
        self.assertNotEqual(deleted["worktree_digest"], clean["worktree_digest"])
        self._git("restore", "tracked.txt")

        executable = self.root / "executable.sh"
        before_mode = read_git_provenance(self.root)["worktree_digest"]
        executable.chmod(0o755)
        after_mode = read_git_provenance(self.root)["worktree_digest"]
        self.assertNotEqual(after_mode, before_mode)
        executable.chmod(0o644)

        link = self.root / "link"
        os.symlink("tracked.txt", link)
        first_link = read_git_provenance(self.root)["worktree_digest"]
        link.unlink()
        os.symlink("executable.sh", link)
        second_link = read_git_provenance(self.root)["worktree_digest"]
        self.assertNotEqual(second_link, first_link)

    def test_only_versioned_gitignore_rules_exclude_untracked_files(self):
        global_ignore = self.root.parent / f"{self.root.name}-global-ignore"
        global_ignore.write_text(".env\n")
        self.addCleanup(global_ignore.unlink)
        self._git("config", "core.excludesFile", str(global_ignore))
        info_exclude = self.root / ".git/info/exclude"
        info_exclude.write_text("info-only.txt\n")
        baseline = read_git_provenance(self.root)

        global_ignored = self.root / ".env"
        global_ignored.write_text("SECRET=value\n")
        with_global_file = read_git_provenance(self.root)
        self.assertTrue(with_global_file["git_dirty"])
        self.assertNotEqual(
            with_global_file["worktree_digest"],
            baseline["worktree_digest"],
        )
        global_ignored.unlink()

        info_ignored = self.root / "info-only.txt"
        info_ignored.write_text("local\n")
        with_info_file = read_git_provenance(self.root)
        self.assertTrue(with_info_file["git_dirty"])
        self.assertNotEqual(
            with_info_file["worktree_digest"],
            baseline["worktree_digest"],
        )

        repository_ignored = self.root / "ignored/cache.bin"
        repository_ignored.parent.mkdir()
        repository_ignored.write_bytes(b"ignored")
        self.assertEqual(read_git_provenance(self.root), with_info_file)

    def _git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            text=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
