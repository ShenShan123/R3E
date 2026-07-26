"""
pytest — §4.3-1 确定性分诊 triage 单测。

全部用 monkeypatch mock check_rtl_synthesizable，不真跑 yosys，不依赖真库。
"""

from __future__ import annotations

import sys
from pathlib import Path

# microsurgeon_flow/ 加入 path，使 triage 可直接 import
sys.path.insert(0, str(Path(__file__).parent))

import pytest
import triage as triage_mod
from triage import triage


def _patch(monkeypatch, return_value):
    """把 triage 模块内的 check_rtl_synthesizable 替换成固定返回值。"""
    monkeypatch.setattr(triage_mod, "check_rtl_synthesizable",
                        lambda *a, **kw: return_value)


# ── 1. success=True → "backend" ──────────────────────────────────────────────

def test_success_routes_to_backend(monkeypatch, tmp_path):
    _patch(monkeypatch, (True, "Synthesizable (120 lines)", tmp_path / "out.v"))
    assert triage(tmp_path / "top.v", "top", tmp_path) == "backend"


# ── 2. "Synthesis check failed:..." → "frontend" ─────────────────────────────

def test_synthesis_failed_routes_to_frontend(monkeypatch, tmp_path):
    _patch(monkeypatch, (False, "Synthesis check failed:\nmodule 'foo' not found", None))
    assert triage(tmp_path / "top.v", "top", tmp_path) == "frontend"


# ── 3. "Yosys not found" → RuntimeError (环境失败，不得路由到任何域) ────────

def test_yosys_not_found_aborts(monkeypatch, tmp_path):
    _patch(monkeypatch, (False, "Yosys not found", None))
    with pytest.raises(RuntimeError, match="环境失败"):
        triage(tmp_path / "top.v", "top", tmp_path)


# ── 4. "Output not generated" → RuntimeError (暧昧，保守按环境失败) ──────────

def test_output_not_generated_aborts(monkeypatch, tmp_path):
    _patch(monkeypatch, (False, "Output not generated", None))
    with pytest.raises(RuntimeError, match="环境失败"):
        triage(tmp_path / "top.v", "top", tmp_path)


# ── 5. 未识别 message → RuntimeError (白名单保守性) ──────────────────────────

def test_unknown_message_aborts(monkeypatch, tmp_path):
    _patch(monkeypatch, (False, "某未知错误: subprocess timeout after 60s", None))
    with pytest.raises(RuntimeError, match="环境失败"):
        triage(tmp_path / "top.v", "top", tmp_path)
