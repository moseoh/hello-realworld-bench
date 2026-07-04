from __future__ import annotations

import sys
from pathlib import Path

from .config import resolve_run_config
from .runner import run_benchmark


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) not in (2, 3):
        print("Usage: python -m hrw_runner <implementation> <scenario> [variant]", file=sys.stderr)
        return 2

    implementation = args[0]
    scenario = args[1]
    variant = args[2] if len(args) == 3 else None
    root_dir = Path.cwd()

    try:
        config = resolve_run_config(implementation, scenario, variant, root_dir)
        result_dir = run_benchmark(config, root_dir)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Result directory: {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
