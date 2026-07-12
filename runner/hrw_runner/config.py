from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts import ContractDocument, validate_repository_contracts


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
    selected_contracts: Mapping[str, ContractDocument]


def resolve_run_config(
    implementation: str,
    scenario: str,
    variant: str | None,
    root_dir: Path | None = None,
    *,
    load_profile: str | None = None,
    environment_profile: str | None = None,
    measurement_protocol: str | None = None,
    build_profile: str | None = None,
) -> RunConfig:
    root = root_dir or Path.cwd()
    documents = validate_repository_contracts(root)

    resolved_implementation = (
        "java/spring-boot" if implementation == "spring-boot" else implementation
    )
    implementation_document = _find_document(
        documents,
        "implementation",
        resolved_implementation,
    )
    if implementation_document is None:
        raise ValueError(f"Unsupported implementation: {implementation}")

    scenario_document = _find_document(documents, "scenario", scenario)
    if scenario_document is None:
        raise ValueError(f"Unsupported scenario: {scenario}")

    implementation_config = implementation_document.value
    resolved_implementation = str(implementation_config["id"])
    language = str(implementation_config["language"])
    framework = str(implementation_config["framework"])
    app_dir = implementation_document.path.parent
    scenario_config = scenario_document.value
    resolved_scenario = str(scenario_config["id"])
    scenario_dir = scenario_document.path.parent
    resolved_variant = variant or str(implementation_config["default_variant"])
    variant_document = _find_variant_document(
        documents,
        resolved_implementation,
        resolved_variant,
    )
    if variant_document is None:
        raise ValueError(
            f"Unsupported variant for {resolved_implementation}: {resolved_variant}"
        )

    variant_config = variant_document.value
    resolved_variant = str(variant_config["id"])
    variant_file = variant_document.path
    default_profiles = _dict_value(scenario_config, "default_profiles")
    environment_profile_id = (
        environment_profile
        if environment_profile is not None
        else str(default_profiles["environment_profile"])
    )
    measurement_protocol_id = (
        measurement_protocol
        if measurement_protocol is not None
        else str(default_profiles["measurement_protocol"])
    )
    load_profile_id = (
        load_profile
        if load_profile is not None
        else str(default_profiles["load_profile"])
    )
    build_profile_id = (
        build_profile
        if build_profile is not None
        else str(implementation_config["default_build_profile"])
    )
    environment_profile_document = _select_profile_contract(
        documents,
        "environment-profile",
        environment_profile_id,
    )
    measurement_protocol_document = _select_profile_contract(
        documents,
        "measurement-protocol",
        measurement_protocol_id,
    )
    load_profile_document = _select_profile_contract(
        documents,
        "load-profile",
        load_profile_id,
    )
    build_profile_document = _select_profile_contract(
        documents,
        "build-profile",
        build_profile_id,
    )
    environment_profile_config = environment_profile_document.value
    measurement_protocol_config = measurement_protocol_document.value
    load_profile_config = load_profile_document.value
    build_profile_config = build_profile_document.value
    _reject_draft_profile("environment profile", environment_profile_config)
    _reject_draft_profile("measurement protocol", measurement_protocol_config)
    _reject_draft_profile("load profile", load_profile_config)
    _reject_draft_profile("build profile", build_profile_config)

    _validate_environment_profile(environment_profile_config)
    _validate_build_profile(build_profile_config)
    target = _dict_value(scenario_config, "target")
    load = _resolve_load_config(
        _dict_value(scenario_config, "load"),
        load_profile_config,
    )
    startup = _resolve_startup_config(
        scenario_config,
        measurement_protocol_config,
    )
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
        scenario=resolved_scenario,
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
        result_prefix=(language, framework, resolved_variant, resolved_scenario),
        implementation_config=implementation_config,
        environment_profile_config=environment_profile_config,
        measurement_protocol_config=measurement_protocol_config,
        load_profile_config=load_profile_config,
        build_profile_config=build_profile_config,
        selected_contracts={
            "implementation": implementation_document,
            "variant": variant_document,
            "scenario": scenario_document,
            "environment_profile": environment_profile_document,
            "measurement_protocol": measurement_protocol_document,
            "load_profile": load_profile_document,
            "build_profile": build_profile_document,
        },
    )


def _find_document(
    documents: list[ContractDocument],
    kind: str,
    document_id: str,
) -> ContractDocument | None:
    for document in documents:
        if document.kind == kind and document.value["id"] == document_id:
            return document
    return None


def _find_variant_document(
    documents: list[ContractDocument],
    implementation_id: str,
    variant_id: str,
) -> ContractDocument | None:
    for document in documents:
        if (
            document.kind == "variant"
            and document.value["id"] == variant_id
            and document.value["implementation"] == implementation_id
        ):
            return document
    return None


def _select_profile_contract(
    documents: list[ContractDocument],
    kind: str,
    profile_id: str,
) -> ContractDocument:
    document = _find_document(documents, kind, profile_id)
    if document is not None:
        return document
    raise ValueError(f"Unsupported {kind.replace('-', ' ')}: {profile_id}")


def _reject_draft_profile(
    profile_type: str,
    profile_config: dict[str, object],
) -> None:
    if profile_config["status"] == "draft":
        raise ValueError(
            f"Draft {profile_type} '{profile_config['id']}' is not executable."
        )


def _resolve_load_config(
    scenario_load: dict[str, object],
    load_profile_config: dict[str, object],
) -> dict[str, object]:
    load = dict(scenario_load)
    timing = _dict_value(load_profile_config, "timing")
    phases = load_profile_config["phases"]

    if load_profile_config["model"] == "disabled":
        if (
            load_profile_config["executor"] != "none"
            or timing != {"source": "disabled"}
            or phases != []
        ):
            raise _unsupported_profile("load profile", load_profile_config)
        load["enabled"] = False
        return load

    if (
        load_profile_config["model"] == "closed"
        and load_profile_config["executor"] == "constant-vus"
        and timing == {"source": "scenario"}
        and phases
        == [{"source": "scenario", "duration_seconds": None, "vus": None}]
        and load.get("enabled") is True
        and all(
            key in load
            for key in ("tool", "script", "warmup_duration", "test_duration", "vus")
        )
    ):
        return load

    raise _unsupported_profile("load profile", load_profile_config)


def _resolve_startup_config(
    scenario_config: dict[str, object],
    measurement_protocol_config: dict[str, object],
) -> dict[str, object]:
    scenario_kind = str(scenario_config["kind"])
    scenario_id = str(scenario_config["id"])
    expected_evidence = "lifecycle" if scenario_kind == "lifecycle" else "service"
    evidence_family = str(measurement_protocol_config["evidence_family"])
    if evidence_family != expected_evidence:
        raise ValueError(
            f"Incompatible measurement protocol "
            f"'{measurement_protocol_config['id']}' for {scenario_kind} scenario "
            f"'{scenario_id}': expected {expected_evidence} evidence, got "
            f"{evidence_family}."
        )

    trials = int(measurement_protocol_config["trials"])
    if expected_evidence == "lifecycle":
        supported_timing = (
            measurement_protocol_config["timing_source"] == "none"
            and measurement_protocol_config["warmup_seconds"] == 0
            and measurement_protocol_config["measured_seconds"] == 0
        )
    else:
        supported_timing = (
            trials == 1
            and measurement_protocol_config["timing_source"] == "scenario"
            and measurement_protocol_config["warmup_seconds"] is None
            and measurement_protocol_config["measured_seconds"] is None
        )
    if not supported_timing:
        raise _unsupported_profile(
            "measurement protocol",
            measurement_protocol_config,
        )

    startup = dict(_optional_dict_value(scenario_config, "startup"))
    startup["iterations"] = trials
    return startup


def _validate_environment_profile(
    environment_profile_config: dict[str, object],
) -> None:
    if not (
        environment_profile_config["orchestrator"] == "docker-compose"
        and environment_profile_config["load_generator"] == "same-host"
        and environment_profile_config["official"] is False
    ):
        raise _unsupported_profile(
            "environment profile",
            environment_profile_config,
        )


def _validate_build_profile(build_profile_config: dict[str, object]) -> None:
    if not (
        build_profile_config["build_tool"] == "gradle"
        and build_profile_config["dependency_cache"] == "persistent"
        and build_profile_config["image_cache"] == "enabled"
        and build_profile_config["image_input"] == "built-artifact"
    ):
        raise _unsupported_profile("build profile", build_profile_config)


def _unsupported_profile(
    profile_type: str,
    profile_config: dict[str, object],
) -> ValueError:
    return ValueError(
        f"Unsupported {profile_type} '{profile_config['id']}' semantics "
        "for the current runner."
    )


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
