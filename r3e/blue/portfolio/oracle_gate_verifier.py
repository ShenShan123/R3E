"""Runner-owned parser, elaboration, simulation, and oracle verification.

The model proposes only a replacement RTL payload.  This verifier owns all
public benchmark assets, writes the candidate into an isolated runtime
workspace, and emits hash-bound receipts without exposing golden RTL to the
candidate provider.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping

from r3e.grounded.command_runner import GroundedCommandRunner
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_text,
    hash_file,
    hash_payload,
)
from r3e.red.grounded.verilog_ast import (
    VerilogAstViolation,
    changed_token_count,
    module_names,
    normalized_ast_hash,
)
from r3e.blue.portfolio.rtl_ast_materializer import (
    AST_SEMANTIC_PATCH_SCHEMA,
)
from r3e.semantic_repair_bench.oracle_gate import judge


VERIFIER_ID = "runner-owned-public-oracle-gate"
VERIFIER_VERSION = "2"


class PublicOracleVerifierViolation(RuntimeError):
    """Raised when public-case or verifier authority is incomplete."""


def _under(root: Path, raw: str | Path, *, field: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PublicOracleVerifierViolation(
            f"{field} escapes project root"
        ) from exc
    if not path.is_file():
        raise PublicOracleVerifierViolation(f"{field} is missing")
    return path


def public_case_from_manifest(
    row: Mapping[str, Any],
    *,
    project_root: str | Path,
) -> dict[str, Any]:
    """Freeze one public manifest row for runner-only verification."""
    root = Path(project_root).resolve()
    payload = deepcopy(dict(row))
    case_id = str(payload.get("case_id") or "")
    buggy_raw = str(
        payload.get("buggy_rtl") or payload.get("buggy_path") or ""
    )
    golden_raw = str(
        payload.get("golden_rtl") or payload.get("reference_path") or ""
    )
    tb_raw = payload.get("tb_sources") or payload.get(
        "testbench_files"
    ) or []
    deps_raw = payload.get("deps") or []
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    tb_output = str(
        payload.get("tb_output") or metadata.get("sim_output") or ""
    )
    if (
        not case_id
        or not buggy_raw
        or not golden_raw
        or not isinstance(tb_raw, list)
        or not tb_raw
        or not isinstance(deps_raw, list)
        or not tb_output
    ):
        raise PublicOracleVerifierViolation(
            "public manifest row lacks oracle assets"
        )
    buggy = _under(root, buggy_raw, field="buggy RTL")
    golden = _under(root, golden_raw, field="golden RTL")
    testbenches = [
        _under(root, value, field="testbench") for value in tb_raw
    ]
    deps = [_under(root, value, field="dependency") for value in deps_raw]
    assets = [buggy, golden, *testbenches, *deps]
    buggy_source = buggy.read_text(encoding="utf-8")
    top_module = str(payload.get("top_module") or "")
    if not top_module:
        parsed_modules = module_names(buggy_source)
        if len(parsed_modules) != 1:
            raise PublicOracleVerifierViolation(
                "public case requires one unambiguous top module"
            )
        top_module = parsed_modules[0]
    return {
        "case_id": case_id,
        "buggy_rtl_path": str(buggy),
        "buggy_rtl_source": buggy_source,
        "buggy_rtl_hash": hash_payload(buggy_source),
        "golden_rtl": str(golden),
        "deps": [str(path) for path in deps],
        "tb_sources": [str(path) for path in testbenches],
        "tb_output": tb_output,
        "top_module": top_module,
        "sim_timeout": float(payload.get("sim_timeout") or 20.0),
        "manifest_file_hashes": {
            str(path.relative_to(root)): hash_file(path)
            for path in sorted(set(assets))
        },
    }


class PublicManifestOracleVerifier:
    """Verify ACP candidates against a frozen public differential oracle."""

    verifier_id = VERIFIER_ID
    verifier_version = VERIFIER_VERSION
    verifier_hash = hash_payload({
        "verifier_id": VERIFIER_ID,
        "verifier_version": VERIFIER_VERSION,
    })

    def __init__(
        self,
        *,
        project_root: str | Path,
        workspace: str | Path,
        run_context_hash: str,
        maximum_ast_edits: int = 64,
        timeout_seconds: float = 30.0,
        allowed_case_artifact_roots: Iterable[str | Path] = (),
    ):
        self.project_root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.allowed_case_artifact_roots = tuple(
            Path(path).resolve() for path in allowed_case_artifact_roots
        )
        if maximum_ast_edits < 1:
            raise PublicOracleVerifierViolation(
                "maximum_ast_edits must be positive"
            )
        self.maximum_ast_edits = int(maximum_ast_edits)
        yosys = shutil.which("yosys")
        if not yosys:
            raise PublicOracleVerifierViolation(
                "Yosys is required for formal parser/elaboration"
            )
        toolchain_hash = hash_payload({
            "verifier_hash": self.verifier_hash,
            "yosys_executable_hash": hash_file(yosys),
        })
        self.command_runner = GroundedCommandRunner(
            allowed_root=self.workspace,
            artifact_root=self.workspace / "command_artifacts",
            allowed_executables={"yosys": yosys},
            run_context_hash=run_context_hash,
            toolchain_fingerprint_hash=toolchain_hash,
            timeout_seconds=timeout_seconds,
        )
        self.verification_records: list[dict[str, Any]] = []

    def _case_artifact(self, raw: str | Path, *, field: str) -> Path:
        """Resolve a manifest asset or runner-generated case artifact.

        Frozen public assets remain rooted at ``project_root``.  A Grounded
        poison generated by the runner may additionally be supplied from an
        explicitly configured round workspace; no other path is accepted.
        """
        path = Path(raw)
        if not path.is_absolute():
            path = self.project_root / path
        path = path.resolve()
        roots = (self.project_root, *self.allowed_case_artifact_roots)
        if not any(path == root or root in path.parents for root in roots):
            raise PublicOracleVerifierViolation(
                f"{field} escapes configured artifact roots"
            )
        if not path.is_file():
            raise PublicOracleVerifierViolation(f"{field} is missing")
        return path

    def _verify_manifest(self, case: Mapping[str, Any]) -> None:
        expected = case.get("manifest_file_hashes")
        if not isinstance(expected, Mapping) or not expected:
            raise PublicOracleVerifierViolation(
                "candidate verifier requires frozen manifest hashes"
            )
        for relative, digest in expected.items():
            path = _under(
                self.project_root, str(relative), field="manifest asset"
            )
            if hash_file(path) != digest:
                raise PublicOracleVerifierViolation(
                    "public manifest asset hash mismatch"
                )

    def _formal_elaboration(
        self,
        *,
        candidate_id: str,
        candidate: Path,
        candidate_hash: str,
        top_module: str,
        run_dir: Path,
    ) -> dict[str, Any]:
        script = run_dir / "elaborate.ys"
        atomic_write_text(
            script,
            "\n".join([
                f'read_verilog -sv "{candidate}"',
                f"hierarchy -check -top {top_module}",
                "proc",
                "check",
                "",
            ]),
        )
        execution = self.command_runner.run(
            receipt_id=f"{candidate_id}:yosys-elaboration",
            phase="elaborate",
            subject_hash=candidate_hash,
            executable="yosys",
            arguments=["-Q", "-T", "-s", str(script)],
            cwd=run_dir,
            artifact_paths={
                "candidate": candidate,
                "script": script,
            },
            observe=lambda _stdout, _stderr, code, kind: {
                "formal_mode": "parser_elaboration",
                "top_module": top_module,
                "exit_code": code,
                "elaboration_ok": kind == "completed",
            },
        )
        return execution.receipt

    def __call__(
        self,
        *,
        policy: PolicyState,
        case: Mapping[str, Any],
        current_case_evidence: Mapping[str, Any],
        slot: Mapping[str, Any],
        candidate_id: str,
        patch_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._verify_manifest(case)
        if patch_payload.get("candidate_id") != candidate_id:
            raise PublicOracleVerifierViolation(
                "candidate payload identity mismatch"
            )
        replacement = patch_payload.get("replacement_rtl")
        semantic = patch_payload.get("semantic_patch")
        if (
            not isinstance(replacement, str)
            or not replacement
            or not isinstance(semantic, Mapping)
        ):
            raise PublicOracleVerifierViolation(
                "candidate replacement payload is incomplete"
            )
        run_dir = self.workspace / candidate_id
        run_dir.mkdir(parents=True, exist_ok=False)
        candidate = run_dir / "candidate.sv"
        atomic_write_text(candidate, replacement)
        candidate_hash = hash_file(candidate)
        buggy_path = self._case_artifact(
            str(case.get("buggy_rtl_path") or ""),
            field="buggy RTL",
        )
        buggy_source = buggy_path.read_text(encoding="utf-8")

        parse_ok = True
        candidate_modules: tuple[str, ...] = ()
        buggy_modules: tuple[str, ...] = ()
        edits = self.maximum_ast_edits + 1
        candidate_ast_hash = hash_payload({"parse": "failed"})
        buggy_ast_hash = hash_payload({"parse": "failed"})
        try:
            candidate_modules = module_names(replacement)
            buggy_modules = module_names(buggy_source)
            candidate_ast_hash = normalized_ast_hash(replacement)
            buggy_ast_hash = normalized_ast_hash(buggy_source)
            edits = changed_token_count(buggy_source, replacement)
        except VerilogAstViolation:
            parse_ok = False

        declared_modules = semantic.get("changed_modules")
        declared_blocks = semantic.get("changed_blocks")
        declared_nodes = semantic.get("changed_ast_nodes")
        declared_operators = semantic.get("operator_classes")
        normalized_patch = semantic.get("normalized_ast_patch")
        ast_binding_ok = bool(
            isinstance(normalized_patch, Mapping)
            and normalized_patch.get("schema_version")
            == AST_SEMANTIC_PATCH_SCHEMA
            and normalized_patch.get("buggy_ast_hash") == buggy_ast_hash
            and normalized_patch.get("candidate_ast_hash")
            == candidate_ast_hash
            and isinstance(normalized_patch.get("operations"), list)
            and bool(normalized_patch["operations"])
        )
        block_scope_ok = bool(
            isinstance(declared_blocks, list)
            and bool(declared_blocks)
            and (
                policy.configuration["patch_scope"] != "local_block"
                or len(declared_blocks) == 1
            )
        )
        scope_ok = (
            parse_ok
            and ast_binding_ok
            and semantic.get("patch_scope")
            == policy.configuration["patch_scope"]
            and isinstance(declared_modules, list)
            and len(declared_modules) == 1
            and declared_modules[0] in candidate_modules
            and block_scope_ok
            and isinstance(declared_nodes, list)
            and bool(declared_nodes)
            and isinstance(declared_operators, list)
            and bool(declared_operators)
            and candidate_modules == buggy_modules
            and replacement != buggy_source
            and edits <= self.maximum_ast_edits
        )
        top_module = str(case.get("top_module") or "")
        if not top_module and len(candidate_modules) == 1:
            top_module = candidate_modules[0]
        if top_module not in candidate_modules:
            scope_ok = False

        formal_receipt = self._formal_elaboration(
            candidate_id=candidate_id,
            candidate=candidate,
            candidate_hash=candidate_hash,
            top_module=top_module or "invalid_top",
            run_dir=run_dir,
        )
        formal_ok = (
            formal_receipt["result_kind"] == "completed"
            and formal_receipt["observed"].get("elaboration_ok") is True
        )
        oracle_case = {
            "golden_rtl": str(case["golden_rtl"]),
            "deps": list(case["deps"]),
            "tb_sources": list(case["tb_sources"]),
            "tb_output": str(case["tb_output"]),
            "top_module": top_module,
            "sim_timeout": float(case["sim_timeout"]),
        }
        outcome = judge(
            oracle_case,
            candidate,
            run_dir / "oracle",
            evidence_k=int(policy.configuration.get("evidence_k") or 1),
        )
        compile_ok = outcome.stage == "compare"
        oracle_ok = bool(
            parse_ok
            and scope_ok
            and formal_ok
            and compile_ok
            and outcome.ok
        )
        compile_receipt_hash = hash_payload({
            "candidate_hash": candidate_hash,
            "candidate_ast_hash": candidate_ast_hash,
            "stage": outcome.stage,
            "compile_ok": compile_ok,
            "command_hash": outcome.detail.get("command_hash", ""),
            "toolchain_fingerprint_hash": outcome.detail.get(
                "toolchain_fingerprint_hash", ""
            ),
        })
        simulation_receipt_hash = hash_payload({
            "candidate_hash": candidate_hash,
            "stage": outcome.stage,
            "oracle_ok": outcome.ok,
            "mismatch_hash": hash_payload(outcome.mismatch),
            "structured_evidence_hash": hash_payload(outcome.structured),
            "golden_lines": outcome.golden_lines,
            "candidate_lines": outcome.cand_lines,
            "raw_recurrence_hash": hash_payload(
                outcome.detail.get("raw_recurrence", "")
            ),
        })
        record = {
            "schema_version": "r3e-public-oracle-verification-record-v1",
            "candidate_id": candidate_id,
            "case_id": str(case["case_id"]),
            "slot_index": int(slot["slot_index"]),
            "policy_hash": policy.policy_hash,
            "current_case_evidence_hash": hash_payload(
                deepcopy(dict(current_case_evidence))
            ),
            "candidate_hash": candidate_hash,
            "candidate_ast_hash": candidate_ast_hash,
            "parse_ok": parse_ok,
            "scope_ok": scope_ok,
            "formal_elaboration_ok": formal_ok,
            "compile_ok": compile_ok,
            "oracle_ok": oracle_ok,
            "ast_edit_count": edits,
            "compile_receipt_hash": compile_receipt_hash,
            "simulation_receipt_hash": simulation_receipt_hash,
            "formal_receipt_hash": formal_receipt["receipt_hash"],
        }
        record["record_hash"] = hash_payload(record)
        atomic_write_json(run_dir / "verification.json", record)
        self.verification_records.append(record)
        return {
            "parse_ok": parse_ok,
            "scope_ok": scope_ok,
            "compile_ok": compile_ok and formal_ok,
            "compile_receipt_hash": compile_receipt_hash,
            "simulation_receipt_hash": simulation_receipt_hash,
            "formal_receipt_hash": formal_receipt["receipt_hash"],
            "oracle_ok": oracle_ok,
            "changed_modules": (
                len(declared_modules)
                if isinstance(declared_modules, list)
                else 0
            ),
            "changed_blocks": (
                len(declared_blocks)
                if isinstance(declared_blocks, list)
                else 0
            ),
            "ast_edit_count": edits,
        }
