"""formal 判据（替代 oracle_gate，用于无 testbench 的大量 RTL 训练池）.

golden 自作 oracle，verify_equiv(seq_miter/induct, 含 abstract_mul 处理乘法器)判功能等价：
- red 用: candidate(buggy) NOT equiv golden = 真功能 bug(可修毒); golden self-equiv = 可修证书.
- blue 用: candidate(patched) equiv golden = 修复成功.
不需 tb → 解锁 RTL 目录大量无 tb 设计作 in-the-wild 训练池(learning curve 涌现).
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microsurgeon_frontend.semantic.llm_micro_repair import verify_equiv  # noqa: E402
from semantic_repair_bench.formal_protocol import atomic_write_json, hash_payload  # noqa: E402


@dataclass
class FormalOutcome:
    equiv: bool          # candidate 与 golden 功能等价?
    proven: int | None
    total: int | None
    yosys_exit: int | None
    err: str = ""
    proof_method: str = "yosys_equiv_induct"
    proof_artifact: str = ""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def formal_judge(golden, deps, candidate, top_module, work_dir,
                 method="induct", timeout=120, abstract_mul=True) -> FormalOutcome:
    """candidate 功能等价 golden? proven==total>0 且 asserted_ok = equiv.

    method: induct(组合/标识符/restructure) | seq_miter(retiming/时序变换).
    golden 自作参考, 无需 tb.
    """
    golden_path = Path(golden)
    candidate_path = Path(candidate)
    dependency_paths = [Path(d) for d in deps]
    work_path = Path(work_dir)

    # A byte-identical, dependency-free candidate has a complete equivalence
    # certificate.  This avoids false negatives for async FFs/latches that
    # cannot be modeled by the selected Yosys proof flow.
    if not dependency_paths:
        golden_sha256 = _sha256(golden_path)
        candidate_sha256 = _sha256(candidate_path)
        if golden_sha256 == candidate_sha256:
            artifact = work_path / "exact_identity_gate.json"
            payload = {
                "schema": "r3e-exact-identity-gate-v1",
                "verdict": "PASS",
                "proof_method": "exact_sha256",
                "golden_sha256": golden_sha256,
                "candidate_sha256": candidate_sha256,
                "dependency_count": 0,
                "top_module": str(top_module),
            }
            payload["proof_payload_sha256"] = hash_payload(payload)
            atomic_write_json(artifact, payload)
            return FormalOutcome(
                equiv=True,
                proven=1,
                total=1,
                yosys_exit=0,
                proof_method="exact_sha256",
                proof_artifact=str(artifact),
            )

    try:
        r = verify_equiv([golden_path], dependency_paths, candidate_path,
                         top_module, work_path, equiv_method=method,
                         equiv_timeout_sec=timeout, abstract_mul=abstract_mul)
    except Exception as e:  # noqa: BLE001
        return FormalOutcome(equiv=False, proven=None, total=None, yosys_exit=None,
                             err=str(e)[:120])
    ok = bool(r["asserted_ok"] and r["proven"] is not None
              and r["proven"] == r["total"] and r["total"] > 0)
    return FormalOutcome(
        equiv=ok,
        proven=r["proven"],
        total=r["total"],
        yosys_exit=r["yosys_exit"],
        proof_method=f"yosys_equiv_{method}",
        proof_artifact=str(r.get("log_path") or ""),
    )
