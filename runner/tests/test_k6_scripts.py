import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    ROOT / "scenarios/ping-api/k6.js",
    ROOT / "scenarios/transactional-command-api/k6.js",
    ROOT / "scenarios/io-aggregation-api/k6.js",
    ROOT / "scenarios/read-heavy-query-api/k6.js",
)
READ_HEAVY_SCRIPT = ROOT / "scenarios/read-heavy-query-api/k6.js"
SERVICE_INPUT_SCRIPTS = (
    ROOT / "scenarios/transactional-command-api/k6.js",
    ROOT / "scenarios/io-aggregation-api/k6.js",
)


@unittest.skipUnless(shutil.which("k6"), "k6 is required to inspect scripts")
class K6ScriptOptionsTest(unittest.TestCase):
    def inspect(self, script: Path, *environment: str) -> dict:
        self.assertTrue(script.is_file())
        command = ["k6", "inspect", "--execution-requirements"]
        for value in environment:
            command.extend(("--env", value))
        command.append(str(script))
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_preserves_constant_vus_defaults(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                inspected = self.inspect(script)
                default = inspected["scenarios"]["default"]

                self.assertEqual(default["executor"], "constant-vus")
                self.assertEqual(
                    inspected["summaryTrendStats"],
                    ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
                )

    def test_supports_explicit_constant_vus(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                inspected = self.inspect(
                    script,
                    "HRW_LOAD_EXECUTOR=constant-vus",
                    "VUS=40",
                    "DURATION=20s",
                )
                default = inspected["scenarios"]["default"]

                self.assertEqual(default["executor"], "constant-vus")
                self.assertEqual(default["vus"], 40)
                self.assertEqual(default["duration"], "20s")

    def test_supports_constant_arrival_rate(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                inspected = self.inspect(
                    script,
                    "HRW_LOAD_EXECUTOR=constant-arrival-rate",
                    "HRW_LOAD_RATE=120",
                    "DURATION=20s",
                    "HRW_LOAD_PRE_ALLOCATED_VUS=30",
                    "HRW_LOAD_MAX_VUS=80",
                )
                default = inspected["scenarios"]["default"]

                self.assertEqual(default["executor"], "constant-arrival-rate")
                self.assertEqual(default["rate"], 120)
                self.assertEqual(default["duration"], "20s")
                self.assertEqual(default["preAllocatedVUs"], 30)
                self.assertEqual(default["maxVUs"], 80)

    def test_supports_ramping_arrival_rate(self):
        stages = '[{"duration":"10s","target":40},{"duration":"20s","target":100}]'
        for script in SCRIPTS:
            with self.subTest(script=script):
                inspected = self.inspect(
                    script,
                    "HRW_LOAD_EXECUTOR=ramping-arrival-rate",
                    "HRW_LOAD_RATE=5",
                    f"HRW_LOAD_STAGES={stages}",
                    "HRW_LOAD_PRE_ALLOCATED_VUS=25",
                    "HRW_LOAD_MAX_VUS=75",
                )
                default = inspected["scenarios"]["default"]

                self.assertEqual(default["executor"], "ramping-arrival-rate")
                self.assertEqual(default["startRate"], 5)
                self.assertEqual(default["stages"], json.loads(stages))
                self.assertEqual(default["preAllocatedVUs"], 25)
                self.assertEqual(default["maxVUs"], 75)


class K6ScriptDeterminismTest(unittest.TestCase):
    def test_scripts_do_not_use_random_input(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                self.assertTrue(script.is_file())
                source = script.read_text()
                self.assertNotIn("Math.random", source)

    def test_service_inputs_use_global_scenario_iteration(self):
        for script in SERVICE_INPUT_SCRIPTS:
            with self.subTest(script=script):
                self.assertTrue(script.is_file())
                source = script.read_text()
                self.assertIn("import exec from 'k6/execution';", source)
                self.assertIn(
                    "const iteration = exec.scenario.iterationInTest;", source
                )
                self.assertIn("customers[iteration % customers.length]", source)
                self.assertIn(
                    "skus[Math.floor(iteration / customers.length) % skus.length]",
                    source,
                )
                self.assertNotIn("exec.vu.", source)
                self.assertNotIn("__VU", source)
                self.assertNotIn("__ITER", source)

    def test_read_heavy_script_uses_fixed_query_mix_and_response_oracles(self):
        self.assertTrue(READ_HEAVY_SCRIPT.is_file())
        source = READ_HEAVY_SCRIPT.read_text()

        self.assertIn("const categories = [", source)
        self.assertIn("'electronics'", source)
        self.assertIn("'garden'", source)
        self.assertIn("const priceWindows = [", source)
        self.assertIn("{ minPriceCents: 500, maxPriceCents: 25499 }", source)
        self.assertIn("{ minPriceCents: 75500, maxPriceCents: 100499 }", source)
        self.assertIn("const pageSizes = [20, 50];", source)
        self.assertIn("import exec from 'k6/execution';", source)
        self.assertIn("const iteration = exec.scenario.iterationInTest;", source)
        self.assertIn("iteration % 4 === 3", source)
        self.assertIn("${baseUrl}/products?${query}", source)
        self.assertIn("afterPriceCents", source)
        self.assertIn("afterId", source)
        self.assertIn("'status is 200': (r) => r.status === 200", source)
        self.assertIn("r.body.length <= 16384", source)
        self.assertIn("body.items.length <= request.limit", source)
        self.assertIn("body.nextCursor === null", source)


if __name__ == "__main__":
    unittest.main()
