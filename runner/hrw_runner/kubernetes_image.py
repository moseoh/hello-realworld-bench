from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from .commands import run


def build_and_push_image(
    app_dir: Path,
    repository: str,
    git_commit: str,
    java_version: str = "25",
) -> dict[str, object]:
    tag = f"{repository}:{git_commit}"
    build_start = time.perf_counter()
    _gradle_build(app_dir, java_version)
    clean_build_ms = round((time.perf_counter() - build_start) * 1000)
    with tempfile.TemporaryDirectory() as temp_dir:
        metadata_path = Path(temp_dir) / "build-metadata.json"
        start = time.perf_counter()
        run(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/amd64",
                "--push",
                "--tag",
                tag,
                "--metadata-file",
                str(metadata_path),
                str(app_dir),
            ]
        )
        build_push_ms = round((time.perf_counter() - start) * 1000)
        metadata = json.loads(metadata_path.read_text())

    digest = _digest(metadata)
    return {
        "tag": tag,
        "image": f"{repository}@{digest}",
        "digest": digest,
        "platform": "linux/amd64",
        "clean_build_ms": clean_build_ms,
        "image_build_push_ms": build_push_ms,
        "distribution": "registry-push",
    }


def build_and_export_image(
    app_dir: Path,
    repository: str,
    git_commit: str,
    output_path: Path,
    java_version: str = "25",
) -> dict[str, object]:
    tag = f"{repository}:{git_commit}"
    builder = _ensure_oci_builder()
    build_start = time.perf_counter()
    _gradle_build(app_dir, java_version)
    clean_build_ms = round((time.perf_counter() - build_start) * 1000)
    with tempfile.TemporaryDirectory() as temp_dir:
        metadata_path = Path(temp_dir) / "build-metadata.json"
        image_start = time.perf_counter()
        run(
            [
                "docker",
                "buildx",
                "build",
                "--builder",
                builder,
                "--platform",
                "linux/amd64",
                "--output",
                f"type=oci,dest={output_path}",
                "--tag",
                tag,
                "--metadata-file",
                str(metadata_path),
                str(app_dir),
            ]
        )
        image_build_ms = round((time.perf_counter() - image_start) * 1000)
        metadata = json.loads(metadata_path.read_text())
    digest = _digest(metadata)
    return {
        "tag": tag,
        "image": f"{repository}@{digest}",
        "digest": digest,
        "platform": "linux/amd64",
        "clean_build_ms": clean_build_ms,
        "image_build_ms": image_build_ms,
        "distribution": "k3s-containerd-import",
    }


def _gradle_build(app_dir: Path, java_version: str) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "GRADLE_USER_HOME=/workspace/.gradle-cache",
            "-v",
            f"{app_dir}:/workspace",
            "-w",
            "/workspace",
            f"eclipse-temurin:{java_version}-jdk",
            "./gradlew",
            "clean",
            "build",
            "--no-daemon",
        ]
    )


def _digest(metadata: dict[str, object]) -> str:
    digest = metadata.get("containerimage.digest")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
    ):
        raise ValueError("Docker build did not return an immutable image digest")
    return digest


def _ensure_oci_builder() -> str:
    name = "hello-realworld-oci"
    try:
        run(["docker", "buildx", "inspect", name], capture=True)
    except subprocess.CalledProcessError:
        run(
            [
                "docker",
                "buildx",
                "create",
                "--name",
                name,
                "--driver",
                "docker-container",
            ]
        )
    return name
