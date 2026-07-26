"""
finish_rpt_parser 单测.

fixture: gcd_p046 真 6_finish.rpt (mtime Jun 9 13:05, 21640B, B3 同期产物).
fixture 路径: microsurgeon_flow/fixtures/gcd_p046_finish.rpt
  - 不进 git (microsurgeon_flow/fixtures/ 已 .gitignore 排除)
  - CI 跑测试前需 cp 一次:
    cp /path/to/OpenROAD-flow-scripts/flow/reports/nangate45/gcd_p046/base/6_finish.rpt \
       microsurgeon_flow/fixtures/gcd_p046_finish.rpt
  - fixture 缺失 -> 标记 skip (不当错误)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from microsurgeon_flow.finish_rpt_parser import parse_reg2reg_endpoint

_FIXTURE = Path(__file__).parent / "fixtures" / "gcd_p046_finish.rpt"


# ── 真 fixture 驱动 (B3 期产物) ────────────────────────────────────────

@pytest.fixture
def real_rpt_text() -> str:
    if not _FIXTURE.exists():
        pytest.skip(f"fixture 缺失: {_FIXTURE} (需 cp 真 rpt 进来)")
    return _FIXTURE.read_text()


def test_extracts_reg2reg_not_port_endpoint(real_rpt_text):
    """关键反例: 不能误抓到 setup 段那条 port endpoint (resp_msg[15])"""
    ep = parse_reg2reg_endpoint(real_rpt_text)
    assert ep is not None
    assert "resp_msg" not in ep["endpoint"], (
        f"误抓到 port endpoint: {ep['endpoint']}"
    )
    # 应该抓到 reg-to-reg 那条
    assert "dpath" in ep["startpoint"]
    assert "dpath" in ep["endpoint"]


def test_extracts_correct_b3_endpoint_values(real_rpt_text):
    """B3 期 gcd_p046 reg-to-reg 已知值: a_reg[14] -> b_reg[5], slack=-0.01"""
    ep = parse_reg2reg_endpoint(real_rpt_text)
    assert ep is not None
    assert "dpath.a_reg.out[14]" in ep["startpoint"]
    assert "dpath.b_reg.out[5]" in ep["endpoint"]
    assert ep["slack"] == pytest.approx(-0.01, abs=1e-6)
    assert ep["module_prefix"] == "dpath"


# ── 容错反例 (无 fixture 依赖, 纯字符串) ───────────────────────────────

def test_empty_input_returns_none():
    assert parse_reg2reg_endpoint("") is None


def test_no_section_header_returns_none():
    """rpt 里完全没 'finish report_checks -path_delay max reg to reg' 段"""
    text = """
some random output
Startpoint: foo.bar
Endpoint: baz.qux
   -0.5   slack (VIOLATED)
"""
    assert parse_reg2reg_endpoint(text) is None


def test_section_all_met_returns_none():
    """段头存在但段内无 VIOLATED (全 MET)"""
    text = """\
finish report_checks -path_delay max reg to reg
--------------------------------------------------------------------------
Startpoint: dpath.a_reg.out[0]$_DFF_P_
Endpoint:   dpath.b_reg.out[0]$_DFF_P_
   0.05   slack (MET)
"""
    assert parse_reg2reg_endpoint(text) is None


def test_section_missing_endpoint_returns_none():
    """段头存在、有 startpoint 和 slack VIOLATED, 但缺 Endpoint 行"""
    text = """\
finish report_checks -path_delay max reg to reg
--------------------------------------------------------------------------
Startpoint: dpath.a_reg.out[0]$_DFF_P_
   -0.01   slack (VIOLATED)
"""
    assert parse_reg2reg_endpoint(text) is None


def test_does_not_match_port_section_alone():
    """段头是 'max' 不是 'max reg to reg' -> 不该被匹配"""
    text = """\
finish report_checks -path_delay max
--------------------------------------------------------------------------
Startpoint: dpath.a_reg.out[14]$_DFFE_PP_
Endpoint:   resp_msg[15] (output port)
   -0.06   slack (VIOLATED)
"""
    assert parse_reg2reg_endpoint(text) is None
