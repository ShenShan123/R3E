"""Fail-closed poison validity gate."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from r3e.protocol.hashing import hash_file, hash_payload


class FormalStatus(str, Enum):
    PROVEN_EQUIV = "PROVEN_EQUIV"
    PROVEN_NON_EQUIV = "PROVEN_NON_EQUIV"
    INCONCLUSIVE = "INCONCLUSIVE"
    TIMEOUT = "TIMEOUT"
    TOOL_ERROR = "TOOL_ERROR"


@dataclass(frozen=True)
class ValidityResult:
    proven_valid: bool
    checks: dict[str, bool]
    rejection_reasons: list[str]
    evidence: dict[str, Any]

    @property
    def result_hash(self) -> str:
        return hash_payload({
            "proven_valid": self.proven_valid,
            "checks": self.checks,
            "rejection_reasons": self.rejection_reasons,
            "evidence": self.evidence,
        })


def validity_gate(poison: dict[str, Any]) -> ValidityResult:
    golden = Path(str(poison.get("golden_rtl") or poison.get("golden") or ""))
    buggy = Path(str(poison.get("buggy_rtl") or poison.get("buggy") or ""))
    formal_status = str(poison.get("formal_status") or "")
    allowed_scope = (
        int(poison.get("changed_modules") or 1) <= 1
        and int(poison.get("changed_blocks") or 1) <= 1
        and int(poison.get("composition_depth") or 1) <= 2
        and not poison.get("testbench_modified")
        and not poison.get("tool_config_modified")
        and not poison.get("sdc_modified")
    )
    checks = {
        "golden_exists": golden.is_file(),
        "buggy_exists": buggy.is_file(),
        "golden_compile": bool(poison.get("golden_compile_ok")),
        "golden_oracle": bool(poison.get("golden_oracle_ok")),
        "buggy_compile": bool(poison.get("buggy_compile_ok")),
        "buggy_functional_fail": bool(poison.get("buggy_functional_fail")),
        "formal_proven_non_equiv": formal_status == FormalStatus.PROVEN_NON_EQUIV.value,
        "output_complete": bool(poison.get("output_complete")),
        "scope_allowed": allowed_scope,
        "revert_pass": bool(poison.get("revert_oracle_ok")),
        "fresh_output": bool(poison.get("fresh_output")),
    }
    hashes_differ = False
    if checks["golden_exists"] and checks["buggy_exists"]:
        hashes_differ = hash_file(golden) != hash_file(buggy)
    checks["rtl_hashes_differ"] = hashes_differ
    reasons = [name for name, passed in checks.items() if not passed]
    evidence = {
        "golden_hash": hash_file(golden) if golden.is_file() else "",
        "buggy_hash": hash_file(buggy) if buggy.is_file() else "",
        "formal_status": formal_status,
        "oracle_result_hash": str(poison.get("oracle_result_hash") or ""),
        "toolchain_fingerprint_hash": str(poison.get("toolchain_fingerprint_hash") or ""),
        "command_hash": str(poison.get("command_hash") or ""),
    }
    return ValidityResult(not reasons, checks, reasons, evidence)
