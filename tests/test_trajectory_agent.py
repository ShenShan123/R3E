"""方案 D 刀2: trajectory agent 控制流单测(全 mock, 零真跑零出网)."""

from microsurgeon_flow.trajectory_agent import (
    EcoCandidate,
    EvalResult,
    PathEvidence,
    ProposeResult,
    run_trajectory,
)


_EVID = PathEvidence(
    cell_chain=[{"inst": "_568_", "master": "NAND2_X2"}],
    startpoint="b_reg[0]",
    endpoint="b_reg[5]",
    slack=-0.06,
)


def _mk_candidates():
    return [
        EcoCandidate("_568_", "NAND2_X4", "upsize", "放大关键路径"),
        EcoCandidate("_900_", "INV_X1", "downsize", "腾旁路空间(投资型)"),
        EcoCandidate("_569_", "INV_X8", "upsize", "放大次关键"),
    ]


def _propose_normal(evidence, state):
    return ProposeResult(candidates=_mk_candidates())


def _propose_abstain(evidence, state):
    return ProposeResult(is_abstain=True, abstain_reason="结构瓶颈,sizing救不了")


def _read_evid(odb):
    return _EVID


def _init_wns(odb):
    return -0.06


def test_closed_branch():
    def eval_fn(candidate, odb):
        return EvalResult(new_wns=0.01, cand_odb_path=odb + ".c")

    def select_fn(evaluated, state):
        return 0

    result = run_trajectory(
        "init.odb", 0.46, "gcd",
        _propose_normal, eval_fn, select_fn, _read_evid, _init_wns,
    )
    assert result.outcome == "closed"
    assert len(result.steps) == 1


def test_abstain_branch():
    called = {"eval": 0}

    def eval_fn(candidate, odb):
        called["eval"] += 1
        return EvalResult(-0.06, odb)

    result = run_trajectory(
        "init.odb", 0.46, "gcd",
        _propose_abstain, eval_fn, lambda evaluated, state: 0,
        _read_evid, _init_wns,
    )
    assert result.outcome == "abstain"
    assert result.abstain_reason == "结构瓶颈,sizing救不了"
    assert called["eval"] == 0


def test_exhausted_early_stop():
    def eval_fn(candidate, odb):
        return EvalResult(new_wns=-0.06, cand_odb_path=odb + ".c")

    result = run_trajectory(
        "init.odb", 0.46, "gcd",
        _propose_normal, eval_fn, lambda evaluated, state: 0,
        _read_evid, _init_wns,
    )
    assert result.outcome == "exhausted"
    assert len(result.steps) == 3


def test_max_steps_cap():
    calls = {"n": 0}

    def eval_fn(candidate, odb):
        step_idx = calls["n"] // 3 + 1
        calls["n"] += 1
        return EvalResult(
            new_wns=-0.06 + 0.001 * step_idx,
            cand_odb_path=odb + ".c",
        )

    result = run_trajectory(
        "init.odb", 0.46, "gcd",
        _propose_normal, eval_fn, lambda evaluated, state: 0,
        _read_evid, _init_wns,
        max_steps=15,
    )
    assert result.outcome == "max_steps"
    assert len(result.steps) == 15


def test_select_non_max():
    def eval_fn(candidate, odb):
        wns_by_inst = {"_568_": -0.05, "_900_": -0.10, "_569_": -0.07}
        return EvalResult(
            new_wns=wns_by_inst[candidate.target_inst],
            cand_odb_path=odb + candidate.target_inst,
        )

    def select_fn(evaluated, state):
        return 1

    result = run_trajectory(
        "init.odb", 0.46, "gcd",
        _propose_normal, eval_fn, select_fn, _read_evid, _init_wns,
    )
    first_step = result.steps[0]
    assert first_step.selected_idx == 1
    argmax = first_step.eval_wns.index(max(first_step.eval_wns))
    assert first_step.selected_idx != argmax
    assert first_step.candidates[1].action_class == "downsize"
    assert "投资型" in first_step.select_reason


def test_best_so_far_early_stop():
    calls = {"n": 0}
    wns_by_step = [-0.02, -0.08, -0.08, -0.08, -0.08]

    def eval_fn(candidate, odb):
        step_idx = calls["n"] // 3
        calls["n"] += 1
        return EvalResult(new_wns=wns_by_step[step_idx], cand_odb_path=odb + ".c")

    result = run_trajectory(
        "init.odb", 0.46, "gcd",
        _propose_normal, eval_fn, lambda evaluated, state: 0,
        _read_evid, _init_wns,
    )
    assert result.outcome == "exhausted"
    assert result.steps[0].wns_after == -0.02
    assert len(result.steps) == 4


def test_action_class_sequence():
    def eval_fn(candidate, odb):
        return EvalResult(new_wns=-0.06, cand_odb_path=odb + ".c")

    def select_fn(evaluated, state):
        return 1 if len(state.steps_so_far) == 0 else 0

    result = run_trajectory(
        "init.odb", 0.46, "gcd",
        _propose_normal, eval_fn, select_fn, _read_evid, _init_wns,
    )
    assert result.action_class_sequence[0] == "downsize"
    assert all(action in ("upsize", "downsize")
               for action in result.action_class_sequence)
    assert result.distilled is False
