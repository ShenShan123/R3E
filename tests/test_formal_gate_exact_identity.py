from __future__ import annotations

import json
from types import SimpleNamespace

from semantic_repair_bench import continual_policy_repair_v2, formal_gate


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
