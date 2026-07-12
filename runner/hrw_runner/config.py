from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import read_contract


@dataclass(frozen=True)
class RunConfig:
    root_dir: Path
    requested_implementation: str
    implementation: str
    language: str
    framework: str
    scenario: str
    variant: str
    app_dir: Path
    scenario_dir: Path
    variant_file: Path
    compose_profile: str
    image_tag: str
    target: dict[str, object]
    load: dict[str, object]
    startup: dict[str, object]
    runtime: dict[str, object]
    scenario_config: dict[str, object]
    variant_config: dict[str, object]
    result_prefix: tuple[str, str, str, str]
    implementation_config: dict[str, object]
    environment_profile_config: dict[str, object]
    measurement_protocol_config: dict[str, object]
    load_profile_config: dict[str, object]
    build_profile_config: dict[str, object]


def resolve_run_config(
    implementation: str,
    scenario: str,
    variant: str | None,
    root_dir: Path | None = None,
) -> RunConfig:
    root = root_dir or Path.cwd()

    if implementation == "spring-boot":
        resolved_implementation = "java/spring-boot"
    elif implementation == "java/spring-boot":
        resolved_implementation = implementation
    else:
        raise ValueError(f"Unsupported implementation: {implementation}")

    language, framework = resolved_implementation.split("/", 1)
    app_dir = root / "implementations" / language / framework
    implementation_file = app_dir / "implementation.yaml"
    scenario_dir = root / "scenarios" / scenario
    scenario_file = scenario_dir / "scenario.yaml"

    if not scenario_file.is_file():
        raise ValueError(f"Unsupported scenario: {scenario}")

    implementation_config = read_contract(
        implementation_file, "implementation", root
    ).value
    resolved_variant = variant or str(implementation_config["default_variant"])
    variant_file = app_dir / "variants" / f"{resolved_variant}.yaml"

    if not variant_file.is_file():
        raise ValueError(
            f"Unsupported variant for {resolved_implementation}: {resolved_variant}"
        )

    variant_config = read_contract(variant_file, "variant", root).value
    scenario_config = read_contract(scenario_file, "scenario", root).value
    contract_references = _dict_value(scenario_config, "contracts")
    environment_profile_config = _read_profile_contract(
        root,
        contract_references,
        "environment_profile",
        "environment-profiles",
        "environment-profile",
    )
    measurement_protocol_config = _read_profile_contract(
        root,
        contract_references,
        "measurement_protocol",
        "measurement-protocols",
        "measurement-protocol",
    )
    load_profile_config = _read_profile_contract(
        root,
        contract_references,
        "load_profile",
        "load-profiles",
        "load-profile",
    )
    build_profile_config = _read_profile_contract(
        root,
        contract_references,
        "build_profile",
        "build-profiles",
        "build-profile",
    )
    target = _dict_value(scenario_config, "target")
    load = _dict_value(scenario_config, "load")
    startup = _optional_dict_value(scenario_config, "startup")
    runtime = _dict_value(variant_config, "runtime")
    docker = _dict_value(variant_config, "docker")
    image_tag = str(
        docker.get("image_tag")
        or f"hello-realworld/{language}-{framework}-{resolved_variant}:local"
    )

    return RunConfig(
        root_dir=root,
        requested_implementation=implementation,
        implementation=resolved_implementation,
        language=language,
        framework=framework,
        scenario=scenario,
        variant=resolved_variant,
        app_dir=app_dir,
        scenario_dir=scenario_dir,
        variant_file=variant_file,
        compose_profile=framework,
        image_tag=image_tag,
        target=target,
        load=load,
        startup=startup,
        runtime=runtime,
        scenario_config=scenario_config,
        variant_config=variant_config,
        result_prefix=(language, framework, resolved_variant, scenario),
        implementation_config=implementation_config,
        environment_profile_config=environment_profile_config,
        measurement_protocol_config=measurement_protocol_config,
        load_profile_config=load_profile_config,
        build_profile_config=build_profile_config,
    )


def _read_profile_contract(
    root_dir: Path,
    references: dict[str, object],
    reference_key: str,
    directory: str,
    kind: str,
) -> dict[str, object]:
    profile_id = str(references[reference_key])
    profile_file = root_dir / "contracts" / directory / f"{profile_id}.yaml"
    return read_contract(profile_file, kind, root_dir).value


def _dict_value(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Expected '{key}' object in YAML configuration")
    return value


def _optional_dict_value(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"Expected '{key}' object in YAML configuration")
    return value
