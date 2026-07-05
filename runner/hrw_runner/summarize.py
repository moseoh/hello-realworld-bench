from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SUMMARY_COLUMNS = [
    "scenario",
    "implementation",
    "variant",
    "ready_ms",
    "rps",
    "p95_ms",
    "error_rate",
    "cpu_percent",
    "memory_usage",
]


def collect_result_rows(root_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for result_path in sorted((root_dir / "results").glob("**/result.json")):
        result = json.loads(result_path.read_text())
        startup = result.get("startup", {})
        metrics = result.get("runtime_metrics", {})
        rows.append(
            {
                "run_id": result.get("run_id"),
                "scenario": result.get("scenario"),
                "implementation": result.get("implementation"),
                "variant": result.get("variant"),
                "ready_ms": startup.get("ready_ms"),
                "rps": metrics.get("rps"),
                "p95_ms": metrics.get("p95_ms"),
                "error_rate": metrics.get("error_rate"),
                "cpu_percent": metrics.get("cpu_percent"),
                "memory_usage": metrics.get("memory_usage"),
            }
        )
    return sorted(rows, key=lambda row: str(row.get("run_id") or ""), reverse=True)


def filter_latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_rows = []
    seen_keys = set()
    for row in sorted(rows, key=lambda item: str(item.get("run_id") or ""), reverse=True):
        key = (row.get("scenario"), row.get("implementation"), row.get("variant"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        latest_rows.append(row)
    return latest_rows


def format_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No result.json files found."

    widths = {
        column: max(len(column), *(len(_format_cell(row.get(column))) for row in rows))
        for column in SUMMARY_COLUMNS
    }
    header = "  ".join(column.ljust(widths[column]) for column in SUMMARY_COLUMNS)
    divider = "  ".join("-" * widths[column] for column in SUMMARY_COLUMNS)
    body = [
        "  ".join(_format_cell(row.get(column)).ljust(widths[column]) for column in SUMMARY_COLUMNS)
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def rows_to_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2) + "\n"


def _format_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)
