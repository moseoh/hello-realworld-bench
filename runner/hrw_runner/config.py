from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunConfig:
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
    result_prefix: tuple[str, str, str, str]


def resolve_run_config(
    implementation: str,
    scenario: str,
    variant: str | None,
    root_dir: Path | None = None,
) -> RunConfig:
    root = root_dir or Path.cwd()
    resolved_variant = variant or "jvm-java25"

    if implementation == "spring-boot":
        resolved_implementation = "java/spring-boot"
    elif implementation == "java/spring-boot":
        resolved_implementation = implementation
    else:
        raise ValueError(f"Unsupported implementation: {implementation}")

    if scenario != "ping-api":
        raise ValueError(f"Unsupported scenario: {scenario}")

    if resolved_variant != "jvm-java25":
        raise ValueError(
            f"Unsupported variant for {resolved_implementation}: {resolved_variant}"
        )

    language, framework = resolved_implementation.split("/", 1)
    app_dir = root / "implementations" / language / framework
    scenario_dir = root / "scenarios" / scenario
    variant_file = app_dir / "variants" / f"{resolved_variant}.yaml"

    return RunConfig(
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
        image_tag=f"hello-realworld/{language}-{framework}-{resolved_variant}:local",
        result_prefix=(language, framework, resolved_variant, scenario),
    )
