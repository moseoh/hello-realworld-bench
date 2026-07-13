import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class WorkflowTrustBoundaryTest(unittest.TestCase):
    def test_official_benchmark_never_runs_for_pull_requests(self):
        workflow = self._load("official-benchmark.yml")

        self.assertEqual(set(workflow["on"]), {"workflow_call"})
        benchmark = workflow["jobs"]["benchmark"]
        self.assertEqual(
            benchmark["runs-on"], ["self-hosted", "linux", "x64", "hrw-home-k3s"]
        )
        self.assertEqual(benchmark["permissions"]["contents"], "read")
        self.assertEqual(benchmark["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["publish"]["permissions"], {"contents": "read"})
        self.assertEqual(
            workflow["jobs"]["publish"]["steps"][0]["with"]["token"],
            "${{ secrets.PUBLIC_REPO_TOKEN }}",
        )
        benchmark_step = next(
            step
            for step in benchmark["steps"]
            if step.get("name") == "Run official qualification set"
        )
        self.assertEqual(benchmark_step["working-directory"], "source")
        self.assertIn("${{ matrix.scenario }}", benchmark_step["run"])
        self.assertIn("${{ matrix.load_profile }}", benchmark_step["run"])
        self.assertEqual(benchmark["strategy"]["max-parallel"], "1")
        self.assertEqual(
            workflow["jobs"]["publish"]["strategy"]["max-parallel"], "1"
        )

    def test_pull_request_ci_uses_only_github_hosted_runner(self):
        workflow = self._load("ci.yml")

        self.assertIn("pull_request", workflow["on"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["check"]["runs-on"], "ubuntu-latest")

    def _load(self, name: str):
        with (ROOT / ".github/workflows" / name).open() as file:
            return yaml.load(file, Loader=yaml.BaseLoader)


if __name__ == "__main__":
    unittest.main()
