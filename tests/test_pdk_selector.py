"""PDK 选择器纯逻辑测: 特征抽取 + schema 约束(mock LLM 不联网)."""
from microsurgeon_flow.pdk_selector import extract_circuit_features, select_pdk, PDK_PROFILES


def test_extract_features(tmp_path):
    v = tmp_path / "d.v"
    v.write_text("module m(input clk, input [7:0]a,b,c, output reg[15:0]y);\n"
                 " always @(posedge clk) y <= a*b + c*a;\nendmodule\n")
    f = extract_circuit_features(v)
    assert f["n_mul"] == 2 and f["n_add"] == 1 and f["n_seq_blocks"] == 1
    assert f["arith_dense"] is False   # 2 < 3


def test_select_schema_constraint():
    # mock LLM 返回越界 pdk → 兜底候选首个; rationale 截 2 句
    def fake(prompt):
        return {"pdk": "magic_pdk", "rationale": "句一。句二。句三。"}
    r = select_pdk({"n_mul": 4}, options=["asap7", "sky130hd"], call_llm=fake)
    assert r["pdk"] == "asap7"                  # 越界 → options[0]
    assert "句三" not in r["rationale"]          # 截 2 句


def test_select_valid_pick():
    r = select_pdk({"n_mul": 0}, options=list(PDK_PROFILES),
                   call_llm=lambda p: {"pdk": "gf180", "rationale": "低成本低频"})
    assert r["pdk"] == "gf180" and r["rationale"] == "低成本低频"


def test_select_llm_error_graceful():
    r = select_pdk({}, call_llm=lambda p: {"llm_call_error": "boom"})
    assert r["pdk"] == "nangate45" and r.get("_llm_failed")
