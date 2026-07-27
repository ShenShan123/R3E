from pathlib import Path
from types import SimpleNamespace

import semantic_repair_bench.correctness_gated_accumulation_curve as curve
from semantic_repair_bench.formal_gate import FormalStatus
from r3e.protocol.hashing import hash_payload


def _design(tmp_path: Path) -> curve.RtlDesign:
    golden = tmp_path / "golden.v"
    golden.write_text("module top(input a, output y); assign y = a; endmodule\n")
    return curve.RtlDesign("d", str(golden), "top", [])


def _fixed_mutation(_golden, work, *, offset):
    work.mkdir(parents=True, exist_ok=True)
    buggy = work / "buggy.v"
    buggy.write_text("module top(input a, output y); assign y = ~a; endmodule\n")
    return {
        "buggy_path": str(buggy),
        "mutation_type": "operator_error",
        "rationale": f"offset {offset}",
    }


def test_gen_poison_rejects_tool_failure_disguised_as_non_equivalence(tmp_path, monkeypatch):
    monkeypatch.setattr(curve, "fixed_mutate_once", _fixed_mutation)
    monkeypatch.setattr(
        curve,
        "formal_judge",
        lambda *_args, **_kwargs: SimpleNamespace(
            equiv=False, proven=None, total=None, yosys_exit=1
        ),
    )
    assert curve.gen_poison(
        _design(tmp_path), tmp_path / "work", formal_timeout=1,
        max_tries=1, mutator="fixed",
    ) is None


def test_gen_poison_rejects_incomplete_proof_without_counterexample(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(curve, "fixed_mutate_once", _fixed_mutation)
    monkeypatch.setattr(
        curve,
        "formal_judge",
        lambda *_args, **_kwargs: SimpleNamespace(
            equiv=False, proven=1, total=2, yosys_exit=1
        ),
    )
    assert curve.gen_poison(
        _design(tmp_path), tmp_path / "work", formal_timeout=1,
        max_tries=1, mutator="fixed",
    ) is None


def test_gen_poison_accepts_hash_bound_counterexample(tmp_path, monkeypatch):
    monkeypatch.setattr(curve, "fixed_mutate_once", _fixed_mutation)
    monkeypatch.setattr(
        curve,
        "formal_judge",
        lambda *_args, **_kwargs: SimpleNamespace(
            equiv=False,
            proven=0,
            total=1,
            yosys_exit=1,
            status=FormalStatus.PROVEN_NON_EQUIV,
            counterexample_hash=hash_payload({"witness": "test"}),
            command_hash=hash_payload({"command": "test"}),
            toolchain_fingerprint_hash=hash_payload({"toolchain": "test"}),
        ),
    )
    poison = curve.gen_poison(
        _design(tmp_path), tmp_path / "work2", formal_timeout=1,
        max_tries=1, mutator="fixed",
    )
    assert poison is not None
    assert poison["admission"]["formal_status"] == "PROVEN_NON_EQUIV"
    assert poison["admission"]["counterexample_hash"].startswith("sha256:")
