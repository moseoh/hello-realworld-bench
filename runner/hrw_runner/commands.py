from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Mapping


def run(
    args: list[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )
