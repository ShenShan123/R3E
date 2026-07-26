"""Reconstructable toolchain fingerprints for oracle and replay records."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .hashing import hash_file, hash_payload


def _version(command: str) -> dict[str, str | None]:
    executable = shutil.which(command)
    if not executable:
        return {"path": None, "version": None}
    try:
        proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        version = (proc.stdout or proc.stderr).splitlines()[0][:300]
    except (OSError, subprocess.SubprocessError) as exc:
        version = f"unavailable:{type(exc).__name__}"
    return {"path": str(Path(executable).resolve()), "version": version}


def fingerprint(
    *,
    tools: Iterable[str] = ("iverilog", "vvp", "yosys"),
    source_files: Iterable[str | Path] = (),
) -> dict:
    payload = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "tools": {name: _version(name) for name in tools},
        "source_files": {
            str(Path(path)): hash_file(path)
            for path in sorted((Path(path) for path in source_files), key=str)
            if Path(path).is_file()
        },
    }
    payload["fingerprint_hash"] = hash_payload(payload)
    return payload
