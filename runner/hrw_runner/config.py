from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


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
    runtime: dict[str, object]
    scenario_config: dict[str, object]
    variant_config: dict[str, object]
    result_prefix: tuple[str, str, str, str]


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
    scenario_dir = root / "scenarios" / scenario
    scenario_file = scenario_dir / "scenario.yaml"

    if not scenario_file.is_file():
        raise ValueError(f"Unsupported scenario: {scenario}")

    scenario_config = _read_yaml(scenario_file)
    resolved_variant = variant or str(scenario_config.get("variant") or "jvm-java25")
    variant_file = app_dir / "variants" / f"{resolved_variant}.yaml"

    if not variant_file.is_file():
        raise ValueError(
            f"Unsupported variant for {resolved_implementation}: {resolved_variant}"
        )

    variant_config = _read_yaml(variant_file)
    target = _dict_value(scenario_config, "target")
    load = _dict_value(scenario_config, "load")
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
        runtime=runtime,
        scenario_config=scenario_config,
        variant_config=variant_config,
        result_prefix=(language, framework, resolved_variant, scenario),
    )


def _read_yaml(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected YAML object in {path}")
    return value


def _dict_value(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Expected '{key}' object in YAML configuration")
    return value
