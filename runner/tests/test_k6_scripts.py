import json
import re
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
        self.assertIn("const tupleIndex = Math.floor(iteration / 4);", source)
        self.assertIn("const continuation = iteration % 4 === 3;", source)
        self.assertIn("const priceInverse = 17679;", source)
        self.assertIn("function firstPageCursor(category, priceWindow, limit)", source)
        self.assertIn("const id = (residue * priceInverse) % 100000 || 100000;", source)
        self.assertIn("Math.floor((id - 1) / 8)", source)
        self.assertIn("query.push(`afterPriceCents=${cursor.priceCents}`, `afterId=${cursor.id}`);", source)
        self.assertNotIn("afterId=1", source)
        self.assertIn("${baseUrl}/products?${query}", source)
        self.assertIn("afterPriceCents", source)
        self.assertIn("afterId", source)
        self.assertIn("'status is 200': (r) => r.status === 200", source)
        self.assertIn("r.body.length <= 16384", source)
        self.assertIn("body.items.length <= request.limit", source)
        self.assertIn("item.category === request.category", source)
        self.assertIn("item.priceCents >= request.minPriceCents", source)
        self.assertIn("item.priceCents <= request.maxPriceCents", source)
        self.assertIn("item.priceCents > previous.priceCents", source)
        self.assertIn("item.id > previous.id", source)
        self.assertIn("item.priceCents > request.cursor.priceCents", source)
        self.assertIn("body.nextCursor === null", source)

    @unittest.skipUnless(shutil.which("k6"), "k6 is required to run dump mode")
    def test_read_heavy_dump_mode_covers_every_tuple_with_correct_cursors(self):
        source = READ_HEAVY_SCRIPT.read_text()
        self.assertIn("HRW_DUMP_REQUESTS", source)

        completed = subprocess.run(
            [
                "k6",
                "run",
                "--quiet",
                "--env",
                "HRW_DUMP_REQUESTS=true",
                "--env",
                "HRW_DUMP_ITERATIONS=256",
                str(READ_HEAVY_SCRIPT),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        records = [
            self._parse_dump_record(value)
            for value in re.findall(
                r"HRW_DUMP:([^\s\"]+)", completed.stdout + completed.stderr
            )
        ]

        self.assertEqual(len(records), 256)
        categories = (
            "electronics",
            "home",
            "books",
            "sports",
            "beauty",
            "toys",
            "automotive",
            "garden",
        )
        price_windows = (
            (500, 25499),
            (25500, 50499),
            (50500, 75499),
            (75500, 100499),
        )
        page_sizes = (20, 50)
        expected_tuples = {
            (category, minimum, maximum, limit)
            for limit in page_sizes
            for minimum, maximum in price_windows
            for category in categories
        }

        self.assertEqual(
            {
                (record["category"], record["minimum"], record["maximum"], record["limit"])
                for record in records
            },
            expected_tuples,
        )

        for iteration, record in enumerate(records):
            with self.subTest(iteration=iteration):
                tuple_index = iteration // 4
                category = categories[tuple_index % len(categories)]
                minimum, maximum = price_windows[
                    (tuple_index // len(categories)) % len(price_windows)
                ]
                limit = page_sizes[
                    (tuple_index // (len(categories) * len(price_windows)))
                    % len(page_sizes)
                ]
                self.assertEqual(
                    record,
                    {
                        "iteration": iteration,
                        "category": category,
                        "minimum": minimum,
                        "maximum": maximum,
                        "limit": limit,
                        "cursor": (
                            self._first_page_cursor(category, minimum, maximum, limit)
                            if iteration % 4 == 3
                            else None
                        ),
                    },
                )

    def _parse_dump_record(self, value: str) -> dict[str, object]:
        iteration, category, minimum, maximum, limit, price, identifier = value.split("|")
        cursor = None
        if price != "null":
            cursor = {"priceCents": int(price), "id": int(identifier)}
        return {
            "iteration": int(iteration),
            "category": category,
            "minimum": int(minimum),
            "maximum": int(maximum),
            "limit": int(limit),
            "cursor": cursor,
        }

    def _first_page_cursor(
        self,
        category: str,
        minimum: int,
        maximum: int,
        limit: int,
    ) -> dict[str, int]:
        categories = (
            "electronics",
            "home",
            "books",
            "sports",
            "beauty",
            "toys",
            "automotive",
            "garden",
        )
        matched = 0
        for price_cents in range(minimum, maximum + 1):
            residue = price_cents - 500
            identifier = (residue * 17679) % 100000 or 100000
            category_index = (
                ((identifier - 1) * 17 + ((identifier - 1) // 8)) % len(categories)
            )
            if categories[category_index] != category or identifier % 20 == 0:
                continue
            matched += 1
            if matched == limit:
                return {"priceCents": price_cents, "id": identifier}
        self.fail("expected a full page")


if __name__ == "__main__":
    unittest.main()
