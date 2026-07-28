"""Runner-owned Yosys SAT formal provider and proof-triplet receipts."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import shutil
from typing import Any, Mapping

from r3e.protocol.hashing import (
    atomic_write_text,
    hash_file,
    hash_payload,
)

from .command_runner import GroundedCommandRunner
from .provider_receipts import (
    build_provider_receipt,
    provider_implementation_hash,
    verify_provider_receipt,
)
from .receipts import verify_command_receipt


YOSYS_FORMAL_PROVIDER_VERSION = "r3e-yosys-formal-provider-v1"
FORMAL_EXECUTION_SCHEMA_VERSION = "r3e-formal-execution-receipt-v1"
FORMAL_TRIPLET_SCHEMA_VERSION = "r3e-formal-proof-triplet-v1"
FORMAL_MODES = {
    "clean_proof",
    "poison_counterexample",
    "revert_proof",
}
FORMAL_VERDICTS = {"proved", "counterexample", "inconclusive"}
_SUCCESS = "SAT proof finished - no model found: SUCCESS!"
_FAIL = "SAT proof finished - model found: FAIL!"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_RECEIPT_PREFIX = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXECUTION_FIELDS = {
    "schema_version",
    "mode",
    "rtl_hash",
    "property_hash",
    "top_module",
    "depth",
    "toolchain_fingerprint",
    "command_receipt",
    "formal_provider_receipt",
    "verdict",
    "counterexample",
    "execution_hash",
}
_TRIPLET_FIELDS = {
    "schema_version",
    "clean",
    "poison",
    "revert",
    "property_hash",
    "toolchain_fingerprint",
    "triplet_hash",
}


class YosysFormalProviderViolation(RuntimeError):
    """Raised when formal evidence is unsafe or not reconstructable."""


def locate_yosys() -> Path:
    path = shutil.which("yosys")
    if not path:
        raise YosysFormalProviderViolation(
            "required formal engine is missing: yosys"
        )
    return Path(path).resolve()


def yosys_toolchain_fingerprint(yosys: Path) -> dict[str, Any]:
    grounded_root = Path(__file__).resolve().parent
    engine = yosys.parent.parent / "libexec" / "yosys"
    if not engine.is_file():
        engine = yosys
    payload = {
        "schema_version": YOSYS_FORMAL_PROVIDER_VERSION,
        "provider_id": "yosys-sat-formal",
        "yosys_hash": hash_file(yosys),
        "yosys_engine_hash": hash_file(engine),
        "formal_parser_provider": "r3e-yosys-sat-formal-v1",
        "formal_parser_implementation_hash": provider_implementation_hash(
            provider_kind="formal_parser",
            provider_id="r3e-yosys-sat-formal",
            provider_version="1",
        ),
        "runner_implementation_hash": hash_payload({
            "command_runner.py": hash_file(
                grounded_root / "command_runner.py"
            ),
            "receipts.py": hash_file(grounded_root / "receipts.py"),
        }),
    }
    payload["toolchain_fingerprint_hash"] = hash_payload(payload)
    return payload


def verify_yosys_toolchain_fingerprint(
    fingerprint: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(fingerprint))
    expected = yosys_toolchain_fingerprint(locate_yosys())
    if payload != expected:
        raise YosysFormalProviderViolation(
            "Yosys toolchain fingerprint differs from local authority"
        )
    return payload


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise YosysFormalProviderViolation(
            f"{field} must be an exact sha256 digest"
        )
    return text


def _parse_verdict(
    stdout: bytes,
    *,
    result_kind: str,
) -> tuple[str, bool]:
    if result_kind != "completed":
        return "inconclusive", False
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "inconclusive", False
    success_count = text.count(_SUCCESS)
    fail_count = text.count(_FAIL)
    if success_count == 1 and fail_count == 0:
        return "proved", False
    if success_count == 0 and fail_count == 1:
        return "counterexample", True
    return "inconclusive", False


def _quote_yosys_path(path: Path) -> str:
    value = str(path)
    if any(character in value for character in ("\n", "\r", "\x00")):
        raise YosysFormalProviderViolation(
            "formal artifact path contains a control character"
        )
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def verify_formal_execution_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(receipt))
    if set(payload) != _EXECUTION_FIELDS:
        raise YosysFormalProviderViolation(
            "formal execution receipt fields mismatch"
        )
    if payload["schema_version"] != FORMAL_EXECUTION_SCHEMA_VERSION:
        raise YosysFormalProviderViolation(
            "formal execution receipt schema mismatch"
        )
    if payload["mode"] not in FORMAL_MODES:
        raise YosysFormalProviderViolation("formal mode is unsupported")
    for field in ("rtl_hash", "property_hash"):
        _digest(payload[field], field)
    if not _IDENTIFIER.fullmatch(str(payload["top_module"] or "")):
        raise YosysFormalProviderViolation(
            "formal top module is not a Verilog identifier"
        )
    if (
        not isinstance(payload["depth"], int)
        or isinstance(payload["depth"], bool)
        or not 1 <= payload["depth"] <= 1024
    ):
        raise YosysFormalProviderViolation(
            "formal depth must be between 1 and 1024"
        )
    toolchain = verify_yosys_toolchain_fingerprint(
        payload["toolchain_fingerprint"]
    )
    command = verify_command_receipt(payload["command_receipt"])
    provider = verify_provider_receipt(
        payload["formal_provider_receipt"]
    )
    if (
        command["phase"] != "formal"
        or command["subject_hash"] != payload["rtl_hash"]
        or command["toolchain_fingerprint_hash"]
        != toolchain["toolchain_fingerprint_hash"]
        or provider["provider_kind"] != "formal_parser"
        or provider["provider_id"] != "r3e-yosys-sat-formal"
        or provider["provider_version"] != "1"
    ):
        raise YosysFormalProviderViolation(
            "formal authority objects are not cross-bound"
        )
    artifacts = command["artifact_hashes"]
    if set(artifacts) != {
        "stdout",
        "stderr",
        "executable",
        "rtl",
        "property",
        "script",
        "trace",
    }:
        raise YosysFormalProviderViolation(
            "formal command artifact fields mismatch"
        )
    expected_inputs = {
        "rtl": payload["rtl_hash"],
        "property": payload["property_hash"],
        "script": artifacts["script"],
        "stdout": artifacts["stdout"],
        "stderr": artifacts["stderr"],
        "trace": artifacts["trace"],
        "command_receipt": command["receipt_hash"],
    }
    expected_result = {
        "mode": payload["mode"],
        "top_module": payload["top_module"],
        "depth": payload["depth"],
        "command_result_kind": command["result_kind"],
        "verdict": payload["verdict"],
        "counterexample": payload["counterexample"],
    }
    if (
        provider["input_artifact_hashes"] != expected_inputs
        or provider["result"] != expected_result
        or command["observed"] != {
            "formal_mode": payload["mode"],
            "top_module": payload["top_module"],
            "depth": payload["depth"],
            "verdict": payload["verdict"],
            "counterexample": payload["counterexample"],
        }
    ):
        raise YosysFormalProviderViolation(
            "formal parser result is not bound to command artifacts"
        )
    if (
        payload["verdict"] not in FORMAL_VERDICTS
        or not isinstance(payload["counterexample"], bool)
        or payload["counterexample"]
        != (payload["verdict"] == "counterexample")
        or (
            command["result_kind"] != "completed"
            and payload["verdict"] != "inconclusive"
        )
    ):
        raise YosysFormalProviderViolation(
            "formal verdict/result contract mismatch"
        )
    if payload["execution_hash"] != hash_payload({
        key: value for key, value in payload.items()
        if key != "execution_hash"
    }):
        raise YosysFormalProviderViolation(
            "formal execution hash mismatch"
        )
    return payload


def verify_formal_proof_triplet(
    triplet: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(triplet))
    if set(payload) != _TRIPLET_FIELDS:
        raise YosysFormalProviderViolation(
            "formal proof triplet fields mismatch"
        )
    if payload["schema_version"] != FORMAL_TRIPLET_SCHEMA_VERSION:
        raise YosysFormalProviderViolation(
            "formal proof triplet schema mismatch"
        )
    clean = verify_formal_execution_receipt(payload["clean"])
    poison = verify_formal_execution_receipt(payload["poison"])
    revert = verify_formal_execution_receipt(payload["revert"])
    toolchain = verify_yosys_toolchain_fingerprint(
        payload["toolchain_fingerprint"]
    )
    if (
        clean["mode"] != "clean_proof"
        or clean["verdict"] != "proved"
        or poison["mode"] != "poison_counterexample"
        or poison["verdict"] != "counterexample"
        or revert["mode"] != "revert_proof"
        or revert["verdict"] != "proved"
        or clean["rtl_hash"] != revert["rtl_hash"]
        or poison["rtl_hash"] == clean["rtl_hash"]
        or payload["property_hash"] != clean["property_hash"]
        or any(
            row["property_hash"] != payload["property_hash"]
            or row["toolchain_fingerprint"] != toolchain
            or row["top_module"] != clean["top_module"]
            or row["depth"] != clean["depth"]
            for row in (clean, poison, revert)
        )
    ):
        raise YosysFormalProviderViolation(
            "formal clean/poison/revert proof contract mismatch"
        )
    if payload["triplet_hash"] != hash_payload({
        key: value for key, value in payload.items()
        if key != "triplet_hash"
    }):
        raise YosysFormalProviderViolation(
            "formal proof triplet hash mismatch"
        )
    return {
        **payload,
        "clean": clean,
        "poison": poison,
        "revert": revert,
        "toolchain_fingerprint": toolchain,
    }


class YosysFormalProvider:
    """Execute one bounded SAT proof without shell or ambient credentials."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        run_context_hash: str,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 4_000_000,
        max_memory_bytes: int = 2_000_000_000,
    ):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.yosys = locate_yosys()
        self.toolchain = yosys_toolchain_fingerprint(self.yosys)
        self.toolchain_hash = self.toolchain[
            "toolchain_fingerprint_hash"
        ]
        self.command_runner = GroundedCommandRunner(
            allowed_root=self.workspace,
            artifact_root=self.workspace / "command_artifacts",
            allowed_executables={"yosys": self.yosys},
            run_context_hash=run_context_hash,
            toolchain_fingerprint_hash=self.toolchain_hash,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_memory_bytes=max_memory_bytes,
        )

    def _artifact(
        self,
        path: str | Path,
        *,
        label: str,
        frozen_hash: str,
    ) -> Path:
        artifact = Path(path).resolve()
        try:
            artifact.relative_to(self.workspace)
        except ValueError as exc:
            raise YosysFormalProviderViolation(
                f"{label} escapes provider workspace"
            ) from exc
        if not artifact.is_file():
            raise YosysFormalProviderViolation(f"{label} is missing")
        _digest(frozen_hash, f"frozen_{label}_hash")
        if hash_file(artifact) != frozen_hash:
            raise YosysFormalProviderViolation(
                f"{label} differs from its frozen manifest"
            )
        return artifact

    def execute(
        self,
        *,
        receipt_prefix: str,
        mode: str,
        rtl_path: str | Path,
        property_path: str | Path,
        top_module: str,
        depth: int,
        frozen_rtl_hash: str,
        frozen_property_hash: str,
    ) -> dict[str, Any]:
        if mode not in FORMAL_MODES:
            raise YosysFormalProviderViolation(
                f"unsupported formal mode: {mode}"
            )
        if not _RECEIPT_PREFIX.fullmatch(receipt_prefix):
            raise YosysFormalProviderViolation(
                "receipt prefix contains unsafe path characters"
            )
        if not _IDENTIFIER.fullmatch(top_module):
            raise YosysFormalProviderViolation(
                "top module is not a Verilog identifier"
            )
        if (
            not isinstance(depth, int)
            or isinstance(depth, bool)
            or not 1 <= depth <= 1024
        ):
            raise YosysFormalProviderViolation(
                "formal depth must be between 1 and 1024"
            )
        rtl = self._artifact(
            rtl_path, label="RTL", frozen_hash=frozen_rtl_hash
        )
        property_file = self._artifact(
            property_path,
            label="property",
            frozen_hash=frozen_property_hash,
        )
        run_dir = self.workspace / "formal_runs" / receipt_prefix
        run_dir.mkdir(parents=True, exist_ok=False)
        script_path = run_dir / "formal.ys"
        trace_path = run_dir / "counterexample.json"
        script = "\n".join([
            (
                "read_verilog -formal -sv "
                f"{_quote_yosys_path(rtl)} "
                f"{_quote_yosys_path(property_file)}"
            ),
            f"prep -top {top_module} -flatten",
            "chformal -lower",
            (
                f"sat -prove-asserts -seq {depth} -show-ports "
                f"-dump_json {_quote_yosys_path(trace_path)}"
            ),
            "",
        ])
        atomic_write_text(script_path, script)
        holder: dict[str, Any] = {}

        def observe(
            stdout: bytes,
            _stderr: bytes,
            _exit_code: int,
            result_kind: str,
        ) -> Mapping[str, Any]:
            verdict, counterexample = _parse_verdict(
                stdout, result_kind=result_kind
            )
            holder.update({
                "verdict": verdict,
                "counterexample": counterexample,
            })
            return {
                "formal_mode": mode,
                "top_module": top_module,
                "depth": depth,
                "verdict": verdict,
                "counterexample": counterexample,
            }

        execution = self.command_runner.run(
            receipt_id=f"{receipt_prefix}:formal",
            phase="formal",
            subject_hash=frozen_rtl_hash,
            executable="yosys",
            arguments=["-Q", "-T", "-s", str(script_path)],
            cwd=run_dir,
            artifact_paths={
                "rtl": rtl,
                "property": property_file,
                "script": script_path,
                "trace": trace_path,
            },
            observe=observe,
        )
        verdict = str(holder["verdict"])
        counterexample = bool(holder["counterexample"])
        command = execution.receipt
        provider = build_provider_receipt(
            provider_kind="formal_parser",
            provider_id="r3e-yosys-sat-formal",
            provider_version="1",
            input_artifact_hashes={
                "rtl": frozen_rtl_hash,
                "property": frozen_property_hash,
                "script": command["artifact_hashes"]["script"],
                "stdout": command["artifact_hashes"]["stdout"],
                "stderr": command["artifact_hashes"]["stderr"],
                "trace": command["artifact_hashes"]["trace"],
                "command_receipt": command["receipt_hash"],
            },
            result={
                "mode": mode,
                "top_module": top_module,
                "depth": depth,
                "command_result_kind": command["result_kind"],
                "verdict": verdict,
                "counterexample": counterexample,
            },
        )
        payload = {
            "schema_version": FORMAL_EXECUTION_SCHEMA_VERSION,
            "mode": mode,
            "rtl_hash": frozen_rtl_hash,
            "property_hash": frozen_property_hash,
            "top_module": top_module,
            "depth": depth,
            "toolchain_fingerprint": deepcopy(self.toolchain),
            "command_receipt": command,
            "formal_provider_receipt": provider,
            "verdict": verdict,
            "counterexample": counterexample,
        }
        payload["execution_hash"] = hash_payload(payload)
        return verify_formal_execution_receipt(payload)

    def execute_triplet(
        self,
        *,
        receipt_prefix: str,
        clean_rtl_path: str | Path,
        poison_rtl_path: str | Path,
        revert_rtl_path: str | Path,
        property_path: str | Path,
        top_module: str,
        depth: int,
        frozen_clean_rtl_hash: str,
        frozen_poison_rtl_hash: str,
        frozen_revert_rtl_hash: str,
        frozen_property_hash: str,
    ) -> dict[str, Any]:
        clean = self.execute(
            receipt_prefix=f"{receipt_prefix}:clean",
            mode="clean_proof",
            rtl_path=clean_rtl_path,
            property_path=property_path,
            top_module=top_module,
            depth=depth,
            frozen_rtl_hash=frozen_clean_rtl_hash,
            frozen_property_hash=frozen_property_hash,
        )
        poison = self.execute(
            receipt_prefix=f"{receipt_prefix}:poison",
            mode="poison_counterexample",
            rtl_path=poison_rtl_path,
            property_path=property_path,
            top_module=top_module,
            depth=depth,
            frozen_rtl_hash=frozen_poison_rtl_hash,
            frozen_property_hash=frozen_property_hash,
        )
        revert = self.execute(
            receipt_prefix=f"{receipt_prefix}:revert",
            mode="revert_proof",
            rtl_path=revert_rtl_path,
            property_path=property_path,
            top_module=top_module,
            depth=depth,
            frozen_rtl_hash=frozen_revert_rtl_hash,
            frozen_property_hash=frozen_property_hash,
        )
        payload = {
            "schema_version": FORMAL_TRIPLET_SCHEMA_VERSION,
            "clean": clean,
            "poison": poison,
            "revert": revert,
            "property_hash": frozen_property_hash,
            "toolchain_fingerprint": deepcopy(self.toolchain),
        }
        payload["triplet_hash"] = hash_payload(payload)
        return verify_formal_proof_triplet(payload)
