"""刀3 + ASAP7(S2) 真实现纯逻辑测(monkeypatch 掉 call_llm/_run_openroad).

真出网/真 ORFS 验证留真跑阶段(隔离根 gate1), 不进单测默认集.
"""
import microsurgeon_flow.backend_eco_oneshot as be
from microsurgeon_flow import pdk_config as pc
from microsurgeon_flow.trajectory_agent import (
    EcoCandidate,
    EvalResult,
    TrajectoryState,
    _scrub_wns_literals,
    make_read_evidence_fn,
    make_select_fn,
)


# ── nangate45: strength 双向 legal ────────────────────────────────────────────
def test_strength_bidirectional_legal_has_down_and_up():
    legal = pc._strength_bidirectional_legal(
        {"u1": "NAND2_X2"}, {"NAND2_X1", "NAND2_X2", "NAND2_X4", "INV_X1"})
    assert legal["u1"] == ["NAND2_X1", "NAND2_X4"]   # 去自身 X2, 含降 X1+升 X4, 不跨族


def test_nangate45_action_class():
    assert pc.action_class(pc.NANGATE45, "NAND2_X2", "NAND2_X4") == "upsize"
    assert pc.action_class(pc.NANGATE45, "NAND2_X4", "NAND2_X1") == "downsize"


# ── asap7: VT-swap legal + action_class ───────────────────────────────────────
_A7_CELLS = {
    "AND2x2_ASAP7_75t_R", "AND2x2_ASAP7_75t_L", "AND2x2_ASAP7_75t_SL",
    "INVx1_ASAP7_75t_R", "INVx1_ASAP7_75t_L",   # 故意缺 SL, 验只提存在的
}


def test_vt_swap_legal_full_triple():
    legal = pc._vt_swap_legal({"u1": "AND2x2_ASAP7_75t_R"}, _A7_CELLS)
    # 去自身 R, 含 L+SL, 不改 drive strength/func
    assert legal["u1"] == ["AND2x2_ASAP7_75t_L", "AND2x2_ASAP7_75t_SL"]


def test_vt_swap_legal_only_existing():
    legal = pc._vt_swap_legal({"u2": "INVx1_ASAP7_75t_R"}, _A7_CELLS)
    assert legal["u2"] == ["INVx1_ASAP7_75t_L"]   # SL 不在 lib → 不提


def test_asap7_action_class_vt_direction():
    # R(0)->SL(2)=faster; SL->R=slower
    assert pc.action_class(pc.ASAP7, "AND2x2_ASAP7_75t_R", "AND2x2_ASAP7_75t_SL") == "vt_faster"
    assert pc.action_class(pc.ASAP7, "AND2x2_ASAP7_75t_SL", "AND2x2_ASAP7_75t_R") == "vt_slower"


def test_asap7_legal_dispatch_via_pdk():
    legal = pc.legal_candidates(pc.ASAP7, {"u1": "AND2x2_ASAP7_75t_R"}, _A7_CELLS)
    assert legal["u1"] == ["AND2x2_ASAP7_75t_L", "AND2x2_ASAP7_75t_SL"]


def test_backend_preflight_derives_action_without_prompt_recall():
    skill = {
        "skill_name": "closed_trajectory",
        "precondition": {
            "backend_outcome": "closed",
            "failure_signature": {"action_class_sequence": ["vt_faster"]},
        },
        "action_template": {"repair_strategy": "blue_llm_eco_vtswap"},
        "validation": {"backend_metric": "closed"},
    }
    actions, sources = be._derive_preflight_actions(
        {"closed": [skill], "routed_unclosed": [], "failed": []},
        {"u1": ["NAND2_X2", "NAND2_X4"]},
    )
    assert len(actions) == 1
    assert actions[0].action_type == "size_cell"
    assert actions[0].target_inst == "u1"
    assert actions[0].params["new_master"] == "NAND2_X2"
    assert sources[0]["skill_name"] == "closed_trajectory"

    prompt = be._build_prompt("plain timing report", {"u1": ["NAND2_X2"]}, -0.06)
    assert "Preflight skill" not in prompt
    assert "历史经验" not in prompt


# ── 共用逻辑 ──────────────────────────────────────────────────────────────────
def test_scrub_wns_literals():
    assert _scrub_wns_literals("downsize 后 WNS=-0.06 赚回 0.1") == \
        "downsize 后 WNS=<num> 赚回 <num>"


def test_read_evidence_prepends_synthetic_header(monkeypatch):
    # 裸 report_checks 风格文本(无 ORFS 段头), adapter 应补头后解析出链+端点
    body = (
        "Startpoint: a_reg (rising)\nEndpoint: b_reg (rising)\nPath Type: max\n\n"
        "   Delay    Time   Description\n"
        "   0.00    0.00 ^ a_reg/CK (DFF_X1)\n"
        "   0.03    0.19 ^ _568_/ZN (NAND2_X2)\n"
        "   0.00    0.52 ^ b_reg/D (DFF_X1)\n"
        "           0.52   data arrival time\n"
        "   -0.06   slack (VIOLATED)\n")
    monkeypatch.setattr(be, "_run_openroad", lambda tcl, **kw: body)
    ev = make_read_evidence_fn("dummy.sdc")("dummy.odb")
    assert [c["inst"] for c in ev.cell_chain] == ["a_reg", "_568_", "b_reg"]
    assert ev.endpoint == "b_reg" and ev.slack == -0.06   # parser 真的咬住了


def _ev(wns):  # 构造 evaluated 项
    return EcoCandidate("u1", "NAND2_X4", "upsize", "r"), EvalResult(wns, "cand.odb")


def test_select_honors_llm_nonmax(monkeypatch):
    monkeypatch.setattr(be, "call_llm",
                        lambda p: {"selected_idx": 1, "select_reason": "先腾空间下步赚回"})
    sf = make_select_fn()
    st = TrajectoryState(steps_so_far=[], cur_wns=-0.10)
    idx = sf([_ev(-0.02), _ev(-0.08)], st)
    assert idx == 1 and sf.reasons == ["先腾空间下步赚回"]


def test_select_llm_error_falls_back_idx0_not_max(monkeypatch):
    monkeypatch.setattr(be, "call_llm", lambda p: {"llm_call_error": "boom"})
    sf = make_select_fn()
    st = TrajectoryState(steps_so_far=[], cur_wns=-0.10)
    assert sf([_ev(-0.08), _ev(-0.02)], st) == 0
