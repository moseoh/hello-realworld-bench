import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hrw_runner.__main__ import main


class CliTest(unittest.TestCase):
    def test_summarize_prints_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize"])

        self.assertEqual(exit_code, 0)
        self.assertIn("scenario", output.getvalue())
        self.assertIn("ping-api", output.getvalue())

    def test_summarize_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"scenario": "ping-api"', output.getvalue())

    def test_summarize_prints_latest_only_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)
            self._write_old_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize", "--latest-only"])

        self.assertEqual(exit_code, 0)
        self.assertIn("1000.25", output.getvalue())
        self.assertNotIn("500.25", output.getvalue())

    def test_summarize_prints_latest_only_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            self._write_result(root_dir)
            self._write_old_result(root_dir)

            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=root_dir), redirect_stdout(output):
                exit_code = main(["summarize", "--latest-only", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertIn('"rps": 1000.25', output.getvalue())
        self.assertNotIn('"rps": 500.25', output.getvalue())

    def _write_result(self, root_dir: Path) -> None:
        result_path = root_dir / "results/java/spring-boot/jvm-java25/ping-api/run/result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            """
{
  "run_id": "2026-07-05T10-00-00_java_spring-boot_jvm-java25_ping-api",
  "scenario": "ping-api",
  "implementation": "java/spring-boot",
  "variant": "jvm-java25",
  "startup": {"ready_ms": 1234},
  "runtime_metrics": {"rps": 1000.25, "p95_ms": 2.5}
}
""".lstrip()
        )

    def _write_old_result(self, root_dir: Path) -> None:
        result_path = root_dir / "results/java/spring-boot/jvm-java25/ping-api/old/result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            """
{
  "run_id": "2026-07-04T10-00-00_java_spring-boot_jvm-java25_ping-api",
  "scenario": "ping-api",
  "implementation": "java/spring-boot",
  "variant": "jvm-java25",
  "startup": {"ready_ms": 2234},
  "runtime_metrics": {"rps": 500.25, "p95_ms": 4.5}
}
""".lstrip()
        )


if __name__ == "__main__":
    unittest.main()
