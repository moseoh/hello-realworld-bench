from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts import ContractDocument, validate_repository_contracts


@dataclass(frozen=True)
class BuildRunConfig:
    root_dir: Path
    requested_implementation: str
    implementation: str
    language: str
    framework: str
    variant: str
    app_dir: Path
    variant_file: Path
    runtime: dict[str, object]
    build: dict[str, object]
    implementation_config: dict[str, object]
    variant_config: dict[str, object]
    environment_profile_config: dict[str, object]
    measurement_protocol_config: dict[str, object]
    build_profile_config: dict[str, object]
    selected_contracts: Mapping[str, ContractDocument]


def resolve_build_run_config(
    implementation: str,
    variant: str | None,
    root_dir: Path | None = None,
    *,
    environment_profile: str,
    measurement_protocol: str,
    build_profile: str,
) -> BuildRunConfig:
    root = root_dir or Path.cwd()
    documents = validate_repository_contracts(root)
    requested_implementation = implementation
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

    implementation_config = implementation_document.value
    resolved_implementation = str(implementation_config["id"])
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

    environment_document = _select_profile_contract(
        documents,
        "environment-profile",
        environment_profile,
    )
    measurement_document = _select_profile_contract(
        documents,
        "measurement-protocol",
        measurement_protocol,
    )
    build_document = _select_profile_contract(
        documents,
        "build-profile",
        build_profile,
    )
    environment_config = environment_document.value
    measurement_config = measurement_document.value
    build_config = build_document.value
    _reject_draft_profile("environment profile", environment_config)
    _reject_draft_profile("measurement protocol", measurement_config)
    _reject_draft_profile("build profile", build_config)
    _validate_build_environment_profile(environment_config)
    _validate_build_measurement_protocol(measurement_config)
    _validate_build_profile(build_config)

    variant_config = variant_document.value
    variant_build = variant_config.get("build")
    if not isinstance(variant_build, dict):
        raise ValueError(
            f"Variant '{variant_config['id']}' does not define build inputs."
        )

    return BuildRunConfig(
        root_dir=root,
        requested_implementation=requested_implementation,
        implementation=resolved_implementation,
        language=str(implementation_config["language"]),
        framework=str(implementation_config["framework"]),
        variant=str(variant_config["id"]),
        app_dir=implementation_document.path.parent,
        variant_file=variant_document.path,
        runtime=_dict_value(variant_config, "runtime"),
        build=variant_build,
        implementation_config=implementation_config,
        variant_config=variant_config,
        environment_profile_config=environment_config,
        measurement_protocol_config=measurement_config,
        build_profile_config=build_config,
        selected_contracts={
            "implementation": implementation_document,
            "variant": variant_document,
            "environment_profile": environment_document,
            "measurement_protocol": measurement_document,
            "build_profile": build_document,
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


def _validate_build_environment_profile(profile: dict[str, object]) -> None:
    build = profile.get("build")
    if not (
        profile["status"] == "frozen"
        and profile["official"] is True
        and profile["orchestrator"] == "host-build"
        and profile["load_generator"] == "none"
        and isinstance(build, dict)
        and build.get("runner_labels")
        == ["self-hosted", "linux", "x64", "hrw-home-k3s"]
        and build.get("platform") == "linux/amd64"
        and build.get("machine_id") == "f66cd2d134b94bb18eb7e531d1baf343"
        and build.get("cpu_model") == "AMD Ryzen 7 5825U"
        and build.get("min_logical_cpus") == 16
        and build.get("min_memory_bytes") == 29313151795
    ):
        raise ValueError(
            f"Unsupported build environment profile '{profile['id']}'."
        )


def _validate_build_measurement_protocol(profile: dict[str, object]) -> None:
    build = profile.get("build")
    if not (
        profile["status"] == "frozen"
        and profile["evidence_family"] == "build"
        and profile["trials"] == 3
        and profile["timing_source"] == "none"
        and profile["warmup_seconds"] == 0
        and profile["measured_seconds"] == 0
        and "lifecycle" not in profile
        and isinstance(build, dict)
        and build.get("start_boundary") == "operation-command-start"
        and build.get("completion_boundary") == "operation-command-exit"
    ):
        raise ValueError(
            f"Unsupported build measurement protocol '{profile['id']}'."
        )


def _validate_build_profile(profile: dict[str, object]) -> None:
    build = profile.get("build")
    if not (
        profile["status"] == "frozen"
        and profile["build_tool"] == "gradle"
        and profile["dependency_cache"] == "immutable-fresh-copy-seed"
        and profile["image_cache"] == "base-only-then-first-package-cache"
        and profile["image_input"] == "built-artifact"
        and isinstance(build, dict)
        and build.get("image_platform") == "linux/amd64"
        and build.get("image_output") == "oci-archive"
        and build.get("workspace") == "fresh-copy"
        and build.get("source_probe") == "0->1"
        and build.get("operations")
        == [
            "gradle_clean_build",
            "image_package",
            "gradle_incremental_rebuild",
            "image_rebuild",
        ]
    ):
        raise ValueError(f"Unsupported build profile '{profile['id']}'.")


def _dict_value(value: dict[str, object], key: str) -> dict[str, object]:
    child = value[key]
    if not isinstance(child, dict):
        raise ValueError(f"Invalid contract field: {key}")
    return child
