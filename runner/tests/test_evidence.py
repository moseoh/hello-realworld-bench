import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hrw_runner.evidence import (
    build_artifact_manifest,
    build_compact_time_series,
    sha256_file,
    summarize_trials,
    validate_run_set_evidence,
)


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


class EvidenceBundleValidationTest(unittest.TestCase):
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
