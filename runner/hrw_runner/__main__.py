from __future__ import annotations

import sys
from pathlib import Path

from .config import resolve_run_config
from .contracts import ContractValidationError, validate_repository_contracts
from .runner import run_benchmark
from .summarize import collect_result_rows, filter_latest_rows, format_table, rows_to_json


_RUN_PROFILE_FLAGS = {
    "--load-profile": "load_profile",
    "--environment-profile": "environment_profile",
    "--measurement-protocol": "measurement_protocol",
    "--build-profile": "build_profile",
}

_USAGE = (
    "Usage: python -m hrw_runner <implementation> <scenario> [variant] "
    "[--load-profile ID] [--environment-profile ID] "
    "[--measurement-protocol ID] [--build-profile ID]\n"
    "       python -m hrw_runner summarize [--latest-only] [--json]\n"
    "       python -m hrw_runner validate"
)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root_dir = Path.cwd()

    if args and args[0] == "validate":
        if len(args) != 1:
            print("Usage: python -m hrw_runner validate", file=sys.stderr)
            return 2
        try:
            documents = validate_repository_contracts(root_dir)
        except ContractValidationError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Validated {len(documents)} contract files.")
        return 0

    if args and args[0] == "summarize":
        summarize_args = args[1:]
        valid_flags = {"--json", "--latest-only"}
        if any(arg not in valid_flags for arg in summarize_args):
            print("Usage: python -m hrw_runner summarize [--latest-only] [--json]", file=sys.stderr)
            return 2
        rows = collect_result_rows(root_dir)
        if "--latest-only" in summarize_args:
            rows = filter_latest_rows(rows)
        if "--json" in summarize_args:
            print(rows_to_json(rows), end="")
        else:
            print(format_table(rows))
        return 0

    parsed_run_args = _parse_run_args(args)
    if parsed_run_args is None:
        print(_USAGE, file=sys.stderr)
        return 2

    implementation, scenario, variant, profile_overrides = parsed_run_args

    try:
        config = resolve_run_config(
            implementation,
            scenario,
            variant,
            root_dir,
            **profile_overrides,
        )
        result_dir = run_benchmark(config, root_dir)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Result directory: {result_dir}")
    return 0


def _parse_run_args(
    args: list[str],
) -> tuple[str, str, str | None, dict[str, str | None]] | None:
    if len(args) < 2 or args[0].startswith("--") or args[1].startswith("--"):
        return None

    implementation, scenario = args[:2]
    index = 2
    variant = None
    if index < len(args) and not args[index].startswith("--"):
        variant = args[index]
        index += 1

    profile_overrides: dict[str, str | None] = {
        name: None for name in _RUN_PROFILE_FLAGS.values()
    }
    seen_flags: set[str] = set()
    while index < len(args):
        flag = args[index]
        if flag not in _RUN_PROFILE_FLAGS or flag in seen_flags:
            return None
        if index + 1 >= len(args) or not args[index + 1] or args[index + 1].startswith("--"):
            return None
        profile_overrides[_RUN_PROFILE_FLAGS[flag]] = args[index + 1]
        seen_flags.add(flag)
        index += 2

    return implementation, scenario, variant, profile_overrides


if __name__ == "__main__":
    raise SystemExit(main())
