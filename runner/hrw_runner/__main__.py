from __future__ import annotations

import sys
from pathlib import Path

from .build_config import resolve_build_run_config
from .build_runner import recover_build_campaign_resources, run_build_benchmark_set
from .config import resolve_run_config
from .contracts import ContractValidationError, validate_repository_contracts
from .publication import publish_run_set
from .runner import run_benchmark, run_benchmark_set
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
    "       python -m hrw_runner run-set <implementation> <scenario> [variant] "
    "[--load-profile ID] [--environment-profile ID] "
    "[--measurement-protocol ID] [--build-profile ID]\n"
    "       python -m hrw_runner build-set <implementation> [variant] "
    "--environment-profile ID --measurement-protocol ID --build-profile ID "
    "[--resource-marker PATH]\n"
    "       python -m hrw_runner build-cleanup <marker-dir>\n"
    "       python -m hrw_runner publish <run-set-dir> <dataset-dir> "
    "--source-commit SHA [--workflow-url URL] "
    "[--raw-artifact-url URL --raw-artifact-sha256 SHA]\n"
    "       python -m hrw_runner validate"
)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root_dir = Path.cwd()

    if args and args[0] == "publish":
        parsed = _parse_publish_args(args[1:])
        if parsed is None:
            print(
                "Usage: python -m hrw_runner publish <run-set-dir> <dataset-dir> "
                "--source-commit SHA [--workflow-url URL] "
                "[--raw-artifact-url URL --raw-artifact-sha256 SHA]",
                file=sys.stderr,
            )
            return 2
        run_set_dir, dataset_dir, options = parsed
        try:
            entry_dir = publish_run_set(
                root_dir / run_set_dir,
                root_dir / dataset_dir,
                root_dir,
                **options,
            )
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Published dataset entry: {entry_dir}")
        return 0

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

    if args and args[0] == "build-cleanup":
        if len(args) != 2 or args[1].startswith("--"):
            print("Usage: python -m hrw_runner build-cleanup <marker-dir>", file=sys.stderr)
            return 2
        try:
            recover_build_campaign_resources(Path(args[1]))
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Recovered build campaign markers: {args[1]}")
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

    if args and args[0] == "build-set":
        parsed_build_args = _parse_build_args(args[1:])
        if parsed_build_args is None:
            print(_USAGE, file=sys.stderr)
            return 2
        implementation, variant, profile_overrides, resource_marker = parsed_build_args
        try:
            config = resolve_build_run_config(
                implementation,
                variant,
                root_dir,
                **profile_overrides,
            )
            result_dir = run_build_benchmark_set(
                config,
                **(
                    {"resource_marker": Path(resource_marker)}
                    if resource_marker is not None
                    else {}
                ),
            )
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Build run set directory: {result_dir}")
        return 0

    run_set_mode = bool(args and args[0] == "run-set")
    run_args = args[1:] if run_set_mode else args
    parsed_run_args = _parse_run_args(run_args)
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
        result_dir = (
            run_benchmark_set(config, root_dir)
            if run_set_mode
            else run_benchmark(config, root_dir)
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    label = "Run set directory" if run_set_mode else "Result directory"
    print(f"{label}: {result_dir}")
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


def _parse_build_args(
    args: list[str],
) -> tuple[str, str | None, dict[str, str], str | None] | None:
    if not args or args[0].startswith("--"):
        return None

    implementation = args[0]
    index = 1
    variant = None
    if index < len(args) and not args[index].startswith("--"):
        variant = args[index]
        index += 1

    flags = {
        "--environment-profile": "environment_profile",
        "--measurement-protocol": "measurement_protocol",
        "--build-profile": "build_profile",
    }
    profile_overrides: dict[str, str] = {}
    resource_marker = None
    seen_flags: set[str] = set()
    while index < len(args):
        flag = args[index]
        if flag in seen_flags:
            return None
        if index + 1 >= len(args) or not args[index + 1] or args[index + 1].startswith("--"):
            return None
        if flag == "--resource-marker":
            resource_marker = args[index + 1]
        elif flag in flags:
            profile_overrides[flags[flag]] = args[index + 1]
        else:
            return None
        seen_flags.add(flag)
        index += 2

    if set(profile_overrides) != set(flags.values()):
        return None
    return implementation, variant, profile_overrides, resource_marker


def _parse_publish_args(
    args: list[str],
) -> tuple[str, str, dict[str, str | None]] | None:
    if len(args) < 4 or args[0].startswith("--") or args[1].startswith("--"):
        return None
    values: dict[str, str | None] = {
        "source_commit": None,
        "workflow_url": None,
        "raw_artifact_url": None,
        "raw_artifact_sha256": None,
    }
    flags = {
        "--source-commit": "source_commit",
        "--workflow-url": "workflow_url",
        "--raw-artifact-url": "raw_artifact_url",
        "--raw-artifact-sha256": "raw_artifact_sha256",
    }
    index = 2
    seen: set[str] = set()
    while index < len(args):
        flag = args[index]
        if flag not in flags or flag in seen or index + 1 >= len(args):
            return None
        value = args[index + 1]
        if not value or value.startswith("--"):
            return None
        values[flags[flag]] = value
        seen.add(flag)
        index += 2
    if values["source_commit"] is None:
        return None
    return args[0], args[1], values


if __name__ == "__main__":
    raise SystemExit(main())
