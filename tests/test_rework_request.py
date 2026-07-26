"""返修请求生成器(回环B接线契约)纯逻辑测: 功能名抽取/路径统计/schema约束/RTL级剥门级."""
from microsurgeon_flow import rework_request as rr
from microsurgeon_flow.trajectory_agent import PathEvidence


def test_func_of():
    assert rr._func_of("AND2x2_ASAP7_75t_R") == "AND2"
    assert rr._func_of("INVx2_ASAP7_75t_SL") == "INV"
    assert rr._func_of("HAxp5_ASAP7_75t_SL") == "HA"      # 分数驱动 xp5
    assert rr._func_of("NAND2_X4") == "NAND2"             # nangate


def test_path_stats():
    chain = [
        {"master": "DFF_X1"}, {"master": "NAND2_X2"}, {"master": "NAND2_X1"},
        {"master": "INV_X1"}, {"master": "DFF_X1"},
    ]
    depth, dom = rr._path_stats(chain)
    assert depth == 3                                      # 非DFF门数(DFF剔除)
    assert dom[0] == "NAND2×2"                             # 主导


def test_module_of():
    assert rr._module_of("dpath.b_reg.out[0]$_DFFE_PP_") == "dpath"
    assert rr._module_of("resp_msg[2]") == ""


_PROB = {"startpoint": "a", "endpoint": "b", "module": "m",
         "residual_gap_ps": -20, "comb_depth": 5}
_ATT = {"tried": ["vt_swap"], "improved_ps": 10, "exhausted_reason": "x"}


def test_recommend_schema_constraint():
    # mock LLM 返回越界 action + 多句 rationale → schema 强约束修正
    def fake(prompt):
        return {"action": "magic", "rationale": "第一句。第二句也很长。", "hint": "x"}
    rec = rr._llm_recommend(fake, _PROB, _ATT, ["NAND2×2"])
    assert rec["action"] == "retime"                      # 越界→兜底 retime
    assert rec["rationale"] == "第一句"                    # 截 1 句


def test_recommend_llm_error_graceful():
    rec = rr._llm_recommend(lambda p: {"llm_call_error": "boom"}, _PROB, _ATT, [])
    assert rec["action"] == "retime" and rec.get("_llm_failed")


def test_build_rework_request_structure_and_rtl_level():
    ev = PathEvidence(
        cell_chain=[{"master": "DFF_X1", "inst": "a"}, {"master": "_26xxx_NAND2_X2", "inst": "_26xxx_"},
                    {"master": "DFF_X1", "inst": "b"}],
        startpoint="dpath.b_reg$_DFF_", endpoint="dpath.a_reg$_DFF_", slack=-50.0)
    req = rr.build_rework_request(
        ev, tried=["vt_swap"], improved_ps=30, residual_gap_ps=-20,
        exhausted_reason="vt_headroom_exhausted",
        call_llm=lambda p: {"action": "retime", "rationale": "r", "hint": "h"})
    # 三段齐
    assert set(req) >= {"problem", "backend_attempt", "recommended_fix"}
    # RTL 级: problem 只含寄存器/模块, 不泄门级 inst 名(_26xxx_)
    assert req["problem"]["startpoint"] == "dpath.b_reg$_DFF_"
    assert "_26xxx_" not in str(req["problem"])
    assert req["problem"]["module"] == "dpath"
    assert req["backend_attempt"]["improved_ps"] == 30
    assert req["recommended_fix"]["action"] == "retime"
