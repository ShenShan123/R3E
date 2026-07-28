"""Restricted subprocess runner that owns Grounded Runtime receipts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from r3e.protocol.hashing import hash_file, hash_payload

from .receipts import build_command_receipt


class GroundedCommandViolation(RuntimeError):
    """Raised before execution when a command exceeds frozen authority."""


@dataclass(frozen=True)
class GroundedCommandExecution:
    receipt: dict[str, Any]
    stdout: bytes
    stderr: bytes


def _bytes_hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class GroundedCommandRunner:
    """Run one argv-only command within a bounded workspace.

    The runner never invokes a shell, inherits no caller secrets, constrains
    wall time/address space/output-file size, and terminates the whole process
    group on timeout.
    """

    def __init__(
        self,
        *,
        allowed_root: str | Path,
        artifact_root: str | Path,
        allowed_executables: Mapping[str, str | Path],
        run_context_hash: str,
        toolchain_fingerprint_hash: str,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 1_000_000,
        max_memory_bytes: int = 1_000_000_000,
    ):
        self.allowed_root = Path(allowed_root).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        try:
            self.artifact_root.relative_to(self.allowed_root)
        except ValueError as exc:
            raise GroundedCommandViolation(
                "artifact root escapes the allowed root"
            ) from exc
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.temporary_root = self.allowed_root / ".grounded-tmp"
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        if timeout_seconds <= 0:
            raise GroundedCommandViolation("timeout_seconds must be positive")
        if max_output_bytes < 1024 or max_memory_bytes < 16_000_000:
            raise GroundedCommandViolation("command resource limits are too small")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = int(max_output_bytes)
        self.max_memory_bytes = int(max_memory_bytes)
        self.run_context_hash = run_context_hash
        self.toolchain_fingerprint_hash = toolchain_fingerprint_hash
        executables = {}
        for name, raw_path in allowed_executables.items():
            if not isinstance(name, str) or not name:
                raise GroundedCommandViolation("executable alias is invalid")
            path = Path(raw_path).resolve()
            if not path.is_file() or not os.access(path, os.X_OK):
                raise GroundedCommandViolation(
                    f"allowed executable is unavailable: {name}"
                )
            executables[name] = path
        if not executables:
            raise GroundedCommandViolation("executable allowlist is empty")
        self.allowed_executables = executables

    def _under_root(self, path: str | Path, *, field: str) -> Path:
        value = Path(path).resolve()
        try:
            value.relative_to(self.allowed_root)
        except ValueError as exc:
            raise GroundedCommandViolation(
                f"{field} escapes the allowed root"
            ) from exc
        return value

    def _validate_arguments(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
    ) -> list[str]:
        path_options = {"-o", "-I", "-y", "-Y", "-f", "-c", "-l", "-M"}
        attached_path_options = ("-I", "-y", "-Y")
        normalized = list(arguments)
        next_is_path = False
        for argument in normalized:
            if "\x00" in argument:
                raise GroundedCommandViolation(
                    "command argument contains a NUL byte"
                )
            if next_is_path:
                candidate = Path(argument)
                if not candidate.is_absolute():
                    candidate = cwd / candidate
                self._under_root(candidate, field="command argument path")
                next_is_path = False
                continue
            if argument in path_options:
                next_is_path = True
                continue
            attached = next(
                (
                    prefix for prefix in attached_path_options
                    if argument.startswith(prefix) and len(argument) > len(prefix)
                ),
                None,
            )
            if attached is not None:
                candidate = Path(argument[len(attached):])
                if not candidate.is_absolute():
                    candidate = cwd / candidate
                self._under_root(candidate, field="command argument path")
                continue
            candidate = Path(argument)
            if candidate.is_absolute() or "/" in argument or argument.startswith("."):
                if not candidate.is_absolute():
                    candidate = cwd / candidate
                self._under_root(candidate, field="command argument path")
        if next_is_path:
            raise GroundedCommandViolation(
                "command path option is missing its value"
            )
        return normalized

    def _resource_limits(self) -> None:
        output_limit = self.max_output_bytes
        memory_limit = self.max_memory_bytes
        cpu_limit = max(1, int(math.ceil(self.timeout_seconds)))
        resource.setrlimit(resource.RLIMIT_FSIZE, (output_limit, output_limit))
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
        os.umask(0o077)

    def run(
        self,
        *,
        receipt_id: str,
        phase: str,
        subject_hash: str,
        executable: str,
        arguments: Sequence[str],
        cwd: str | Path,
        artifact_paths: Mapping[str, str | Path] | None = None,
        observe: Callable[[bytes, bytes, int, str], Mapping[str, Any]]
        | None = None,
    ) -> GroundedCommandExecution:
        if not isinstance(receipt_id, str) or not receipt_id:
            raise GroundedCommandViolation("receipt_id must be non-empty")
        if executable not in self.allowed_executables:
            raise GroundedCommandViolation(
                f"executable is not allowlisted: {executable}"
            )
        if (
            not isinstance(arguments, Sequence)
            or isinstance(arguments, (str, bytes))
            or any(not isinstance(value, str) for value in arguments)
        ):
            raise GroundedCommandViolation("command arguments must be strings")
        work_dir = self._under_root(cwd, field="command cwd")
        if not work_dir.is_dir():
            raise GroundedCommandViolation("command cwd does not exist")
        outputs = {
            name: self._under_root(path, field=f"artifact {name}")
            for name, path in (artifact_paths or {}).items()
        }
        normalized_arguments = self._validate_arguments(
            arguments, cwd=work_dir
        )
        artifact_token = hash_payload({
            "receipt_id": receipt_id
        }).split(":", 1)[1]
        artifact_dir = self.artifact_root / artifact_token
        try:
            artifact_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise GroundedCommandViolation(
                "receipt id already has persisted command artifacts"
            ) from exc
        stdout_path = artifact_dir / "stdout.bin"
        stderr_path = artifact_dir / "stderr.bin"
        executable_path = self.allowed_executables[executable]
        argv = [str(executable_path), *normalized_arguments]
        started = time.monotonic()
        timed_out = False
        with stdout_path.open("wb") as stdout_stream, stderr_path.open(
            "wb"
        ) as stderr_stream:
            process = subprocess.Popen(
                argv,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                shell=False,
                env={
                    "PATH": os.pathsep.join(
                        [
                            *sorted({
                                str(path.parent)
                                for path in self.allowed_executables.values()
                            }),
                            "/usr/bin",
                            "/bin",
                        ]
                    ),
                    "LANG": "C",
                    "LC_ALL": "C",
                    "TMPDIR": str(self.temporary_root),
                },
                start_new_session=True,
                close_fds=True,
                preexec_fn=self._resource_limits,
            )
            try:
                exit_code = process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                exit_code = process.wait()
            else:
                # A provider may fork and let its direct process exit first.
                # Ensure no descendant from this isolated process group survives
                # beyond the receipt boundary.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for path in (stdout_path, stderr_path, *outputs.values()):
            if path.is_file():
                descriptor = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        directory_descriptor = os.open(artifact_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        wall_time_ms = int((time.monotonic() - started) * 1000)
        stdout = stdout_path.read_bytes()
        stderr = stderr_path.read_bytes()
        output_limited = (
            len(stdout) >= self.max_output_bytes
            or len(stderr) >= self.max_output_bytes
        )
        crashed = exit_code < 0 and not timed_out and not output_limited
        if timed_out:
            result_kind = "timeout"
        elif output_limited:
            result_kind = "resource_limit"
        elif crashed:
            result_kind = "crash"
        elif exit_code != 0:
            result_kind = "tool_error"
        else:
            result_kind = "completed"
        observed = dict(
            observe(stdout, stderr, exit_code, result_kind)
            if observe is not None
            else {}
        )
        artifact_hashes = {
            "stdout": _bytes_hash(stdout),
            "stderr": _bytes_hash(stderr),
            "executable": hash_file(executable_path),
        }
        for name, path in outputs.items():
            artifact_hashes[name] = (
                hash_file(path) if path.is_file() else hash_payload({
                    "missing_artifact": str(
                        path.relative_to(self.allowed_root)
                    ),
                })
            )
        receipt = build_command_receipt(
            receipt_id=receipt_id,
            phase=phase,
            subject_hash=subject_hash,
            run_context_hash=self.run_context_hash,
            toolchain_fingerprint_hash=self.toolchain_fingerprint_hash,
            command_argv=argv,
            working_directory_hash=hash_payload({
                "relative_cwd": str(work_dir.relative_to(self.allowed_root)),
            }),
            exit_code=exit_code,
            timed_out=timed_out,
            crashed=crashed,
            result_kind=result_kind,
            wall_time_ms=wall_time_ms,
            artifact_hashes=artifact_hashes,
            observed=observed,
        )
        return GroundedCommandExecution(
            receipt=receipt,
            stdout=stdout,
            stderr=stderr,
        )
