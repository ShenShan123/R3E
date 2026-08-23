"""Repository-local helpers shared by the competition facade."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def run_capture(command: list[str], cwd: Path, timeout: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": f"missing_tool:{exc.filename}",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": "timeout",
        }
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "error": "" if completed.returncode == 0 else "command_failed",
    }


def executable_versions() -> dict[str, Any]:
    result: dict[str, Any] = {}
    specs = {
        # Toolchain receipts must describe the interpreter executing the
        # runner, not an unrelated python3 found earlier on PATH.
        "python": (sys.executable, [sys.executable, "--version"]),
        "iverilog": ("iverilog", ["iverilog", "-V"]),
        "vvp": ("vvp", ["vvp", "-V"]),
        "yosys": ("yosys", ["yosys", "-V"]),
    }
    for name, (executable_name, args) in specs.items():
        executable = shutil.which(executable_name)
        if not executable:
            result[name] = {"available": False, "path": None, "version": None}
            continue
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
            text = (proc.stdout or proc.stderr or "").splitlines()
            result[name] = {
                "available": True,
                "path": executable,
                "version": text[0][:300] if text else None,
            }
        except (OSError, subprocess.SubprocessError):
            result[name] = {"available": True, "path": executable, "version": None}
    return result


def git_provenance(root: Path) -> dict[str, Any]:
    def capture(args: list[str]) -> str:
        try:
            return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unavailable"

    return {
        "commit": capture(["rev-parse", "HEAD"]),
        "branch": capture(["branch", "--show-current"]),
        "dirty": bool(capture(["status", "--porcelain"])),
    }
