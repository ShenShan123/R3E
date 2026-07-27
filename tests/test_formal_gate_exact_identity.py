from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from semantic_repair_bench import continual_policy_repair_v2, formal_gate
from microsurgeon_frontend.semantic import llm_micro_repair


def test_exact_identity_gate_bypasses_incomplete_yosys(monkeypatch, tmp_path):
    golden = tmp_path / "golden.v"
    candidate = tmp_path / "candidate.v"
    rtl = "module top(input a, output y); assign y = a; endmodule\n"
    golden.write_text(rtl)
    candidate.write_text(rtl)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("byte-identical candidate must not invoke Yosys")

    monkeypatch.setattr(formal_gate, "verify_equiv", forbidden)
    outcome = formal_gate.formal_judge(
        golden, [], candidate, "top", tmp_path / "judge"
    )

    assert outcome.equiv is True
    assert (outcome.proven, outcome.total, outcome.yosys_exit) == (1, 1, 0)
    assert outcome.proof_method == "exact_sha256"
    proof = json.loads((tmp_path / "judge/exact_identity_gate.json").read_text())
    assert proof["verdict"] == "PASS"
    assert proof["golden_sha256"] == proof["candidate_sha256"]


def test_nonidentical_candidate_still_uses_formal_evaluator(monkeypatch, tmp_path):
    golden = tmp_path / "golden.v"
    candidate = tmp_path / "candidate.v"
    golden.write_text("module top(output y); assign y = 1'b0; endmodule\n")
    candidate.write_text("module top(output y); assign y = 1'b1; endmodule\n")
    called = []

    def fake_verify(*args, **kwargs):
        called.append((args, kwargs))
        return {
            "asserted_ok": False,
            "proven": 0,
            "total": 1,
            "yosys_exit": 1,
            "log_path": str(tmp_path / "judge/equiv_check.log"),
        }

    monkeypatch.setattr(formal_gate, "verify_equiv", fake_verify)
    outcome = formal_gate.formal_judge(
        golden, [], candidate, "top", tmp_path / "judge"
    )

    assert called
    assert outcome.equiv is False
    assert outcome.proof_method == "yosys_equiv_induct"


def test_unproven_equiv_cells_are_inconclusive_without_witness(
    monkeypatch, tmp_path
):
    golden = tmp_path / "golden.v"
    candidate = tmp_path / "candidate.v"
    golden.write_text("module top(output y); assign y = 1'b0; endmodule\n")
    candidate.write_text("module top(output y); assign y = 1'b1; endmodule\n")
    monkeypatch.setattr(
        formal_gate,
        "verify_equiv",
        lambda *_args, **_kwargs: {
            "asserted_ok": False,
            "proven": 1,
            "total": 2,
            "yosys_exit": 1,
            "timed_out": False,
            "log_path": str(tmp_path / "judge/equiv_check.log"),
            "counterexample_found": False,
            "counterexample_hash": "",
        },
    )
    outcome = formal_gate.formal_judge(
        golden, [], candidate, "top", tmp_path / "judge"
    )
    assert outcome.status is formal_gate.FormalStatus.INCONCLUSIVE
    assert not outcome.counterexample_hash


def test_explicit_hash_bound_sat_witness_is_proven_non_equiv(
    monkeypatch, tmp_path
):
    golden = tmp_path / "golden.v"
    candidate = tmp_path / "candidate.v"
    golden.write_text("module top(output y); assign y = 1'b0; endmodule\n")
    candidate.write_text("module top(output y); assign y = 1'b1; endmodule\n")
    witness_hash = "sha256:" + "c" * 64
    monkeypatch.setattr(
        formal_gate,
        "verify_equiv",
        lambda *_args, **_kwargs: {
            "asserted_ok": False,
            "proven": 0,
            "total": 1,
            "yosys_exit": 1,
            "timed_out": False,
            "log_path": str(tmp_path / "judge/equiv_check.log"),
            "counterexample_found": True,
            "counterexample_hash": witness_hash,
        },
    )
    outcome = formal_gate.formal_judge(
        golden, [], candidate, "top", tmp_path / "judge", method="seq_miter"
    )
    assert outcome.status is formal_gate.FormalStatus.PROVEN_NON_EQUIV
    assert outcome.counterexample_hash == witness_hash


def test_seq_miter_parser_emits_counterexample_only_for_explicit_sat_model(
    monkeypatch, tmp_path
):
    golden = tmp_path / "golden.v"
    candidate = tmp_path / "candidate.v"
    golden.write_text("module top(output y); assign y = 1'b0; endmodule\n")
    candidate.write_text("module top(output y); assign y = 1'b1; endmodule\n")

    def fake_run(command, **_kwargs):
        log_path = Path(command[2])
        log_path.write_text(
            "SAT proof finished - model found: FAIL!\n"
            "Signal Name Dec Hex Bin\n"
        )
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(llm_micro_repair.subprocess, "run", fake_run)
    result = llm_micro_repair.verify_equiv(
        [golden],
        [],
        candidate,
        "top",
        tmp_path / "verify",
        equiv_method="seq_miter",
    )
    assert result["counterexample_found"] is True
    assert result["counterexample_hash"].startswith("sha256:")


def test_induct_unproven_cells_never_emit_counterexample(monkeypatch, tmp_path):
    golden = tmp_path / "golden.v"
    candidate = tmp_path / "candidate.v"
    golden.write_text("module top(output y); assign y = 1'b0; endmodule\n")
    candidate.write_text("module top(output y); assign y = 1'b1; endmodule\n")

    def fake_run(command, **_kwargs):
        Path(command[2]).write_text(
            "Of those cells 1 are proven and 1 are unproven.\n"
        )
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(llm_micro_repair.subprocess, "run", fake_run)
    result = llm_micro_repair.verify_equiv(
        [golden],
        [],
        candidate,
        "top",
        tmp_path / "verify-induct",
        equiv_method="induct",
    )
    assert result["counterexample_found"] is False
    assert result["counterexample_hash"] == ""


def test_strategy_candidates_are_preserved_independently(monkeypatch, tmp_path):
    buggy = tmp_path / "buggy.v"
    golden = tmp_path / "golden.v"
    buggy.write_text("module top(output y);\nassign y = 0;\nendmodule\n")
    golden.write_text("module top(output y);\nassign y = 2;\nendmodule\n")
    proposals = [
        {
            "start_line": 2,
            "end_line": 2,
            "new_code": "assign y = 1;",
            "primitive": "decimal_literal_pm1",
            "delta": 1,
        },
        {
            "start_line": 2,
            "end_line": 2,
            "new_code": "assign y = 2;",
            "primitive": "decimal_literal_pm1",
            "delta": 2,
        },
    ]
    monkeypatch.setattr(
        continual_policy_repair_v2,
        "enumerate_strategy_patches",
        lambda *_args, **_kwargs: proposals,
    )
    monkeypatch.setattr(
        continual_policy_repair_v2,
        "formal_judge",
        lambda *_args, **_kwargs: SimpleNamespace(
            equiv=False,
            proven=0,
            total=1,
            yosys_exit=1,
            err="non-equivalent",
            proof_method="test",
            proof_artifact="",
        ),
    )

    class Accumulator:
        template_stage = "promoted"

        @staticmethod
        def template_for(_poison):
            return {
                "template_id": "constant",
                "action_policy": {
                    "planner_authority": "select_repair_primitive_only",
                    "executor_authority": "deterministic_patch_generation_only",
                    "llm_patch_generation_allowed": False,
                    "strategy_primitives": ["decimal_literal_pm1"],
                },
            }

        @staticmethod
        def shadow_template_for(_poison):
            return None

    result = continual_policy_repair_v2.blue_repair_formal_v2(
        {
            "buggy": str(buggy),
            "golden": str(golden),
            "deps": [],
            "top": "top",
            "family": "constant_error",
            "mutation_type": "constant_error",
        },
        tmp_path / "work",
        registry=[],
        accumulator=Accumulator(),
        use_accumulated_templates=True,
        formal_timeout=1,
    )

    paths = [row["patched_rtl"] for row in result["candidates"]]
    assert len(paths) == len(set(paths)) == 2
    assert (tmp_path / "work/primitive_000/buggy.v").is_file()
    assert (tmp_path / "work/primitive_001/buggy.v").is_file()
    assert (tmp_path / "work/primitive_000/buggy.v").read_text() != (
        tmp_path / "work/primitive_001/buggy.v"
    ).read_text()
