"""
pytest — §8.2 Step 4 三库安全闸 + 工厂单测。

所有 I/O 指向 pytest tmp_path 临时目录；真库零触碰。
"""

from __future__ import annotations

import sys
from pathlib import Path

# microsurgeon_flow/ 加入 path，使 three_lib_gate 可直接 import
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from three_lib_gate import (
    _REAL_MEMORY_ROOT,
    make_domain_io,
    resolve_memory_root_or_abort,
)
from distill_loopback import distill_loopback_skill


# ── 1. MEMORY_ROOT 未设 → SystemExit ─────────────────────────────────────────

def test_no_memory_root_aborts(monkeypatch):
    monkeypatch.delenv("MEMORY_ROOT", raising=False)
    with pytest.raises(SystemExit):
        resolve_memory_root_or_abort("loopback")


# ── 2. 非法 domain → SystemExit ──────────────────────────────────────────────

def test_illegal_domain_aborts(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
    with pytest.raises(SystemExit):
        resolve_memory_root_or_abort("foo")


# ── 3. 合法配置 → 子库路径与 guard._domain 正确 ───────────────────────────────

@pytest.mark.parametrize("domain", ["frontend", "backend", "loopback"])
def test_factory_correct_roots(monkeypatch, tmp_path, domain):
    monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
    monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)

    mgr, guard = make_domain_io(domain)

    expected_sublib = (tmp_path / f"{domain}_lib").resolve()
    assert mgr.root.resolve() == expected_sublib, (
        f"mgr.root={mgr.root!r} != expected {expected_sublib!r}"
    )
    assert guard._domain == domain


# ── 4. Gate 3：真库根 / 真库子库均被拦截 ─────────────────────────────────────

def test_real_memory_root_blocked(monkeypatch):
    """MEMORY_ROOT 直接等于真库根 → SystemExit"""
    monkeypatch.setenv("MEMORY_ROOT", str(_REAL_MEMORY_ROOT))
    monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)
    with pytest.raises(SystemExit):
        resolve_memory_root_or_abort("loopback")


def test_real_memory_sublib_blocked(monkeypatch):
    """MEMORY_ROOT 直接指真库的某个子库 → Gate 3 也应拦截（修复绕过漏洞）"""
    monkeypatch.setenv("MEMORY_ROOT", str(_REAL_MEMORY_ROOT / "frontend_lib"))
    monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)
    with pytest.raises(SystemExit):
        resolve_memory_root_or_abort("loopback")


# ── 5. 端到端 vet_distill ────────────────────────────────────────────────────

_LOOPBACK_SKILL = dict(
    skill_name="buf_insert_loopback",
    precondition={"error_signature": "setup_viol", "context_pattern": "reg2reg"},
    action_template={
        "repair_strategy": "insert_buffer",
        "allowed_edit_scope": ["design/top.v"],
    },
    validation={"wns_ok": True},
    rollback_condition={"wns_degradation": True},
)

# backend 域必须带 backend_metric；scope 用第3步验过的 openroad_eco:gcd 形态
_BACKEND_SKILL = dict(
    skill_name="buf_insert_backend",
    precondition={"error_signature": "setup_viol", "context_pattern": "reg2reg"},
    action_template={
        "repair_strategy": "insert_buffer",
        "allowed_edit_scope": ["openroad_eco:gcd"],
    },
    validation={"backend_metric": "WNS_improvement > 0"},
    rollback_condition={"wns_degradation": True},
)


def test_e2e_loopback_vet_distill(monkeypatch, tmp_path):
    """loopback 域：带 case_id → happened=True；缺 case_id → ValueError"""
    monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
    monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)

    mgr, guard = make_domain_io("loopback")

    happened, vet_result, artifact = guard.vet_distill(
        mgr, **_LOOPBACK_SKILL, case_id="CASE-LOOPBACK-001"
    )
    assert happened is True, f"vet_result={vet_result}"
    assert artifact.case_id == "CASE-LOOPBACK-001"

    with pytest.raises(ValueError, match="case_id is required"):
        guard.vet_distill(mgr, **_LOOPBACK_SKILL)  # 无 case_id → distill 抛 ValueError


def test_distill_loopback_skill_has_bounded_frontend_scope(monkeypatch, tmp_path):
    """loopback payload must carry a bounded frontend RTL-block scope."""
    monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
    monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)

    happened, vet_result, artifact = distill_loopback_skill(
        case_id="CASE-LOOPBACK-SCOPE",
        violating_endpoints=[{"endpoint": "resp_msg[15]", "slack": -0.07}],
        frontend_skill_name="frontend_timing_structural_llm_block_rewrite_gcd_L744",
        backend_failure_class="tool_no_effect",
    )

    assert happened is True, f"vet_result={vet_result}"
    scope = artifact.action_template["allowed_edit_scope"]
    assert scope == ["frontend_rtl_block:timing_structural_llm_block_rewrite"]


def test_e2e_backend_vet_distill_and_stat(monkeypatch, tmp_path):
    """backend 域（有 A3 跨域校验）：验证真实域路径走得通，并 stat 确认真库零触碰"""
    monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
    monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)

    # 记录真库 mtime（真库存在时）
    real_mtime_before = (
        _REAL_MEMORY_ROOT.stat().st_mtime if _REAL_MEMORY_ROOT.exists() else None
    )

    mgr, guard = make_domain_io("backend")

    # --- 带 case_id 的合规 backend skill → happened=True --------------------
    happened, vet_result, artifact = guard.vet_distill(
        mgr, **_BACKEND_SKILL, case_id="CASE-BACKEND-001"
    )
    assert happened is True, (
        f"backend vet 未通过！veto_code={vet_result.veto_code}, reason={vet_result.reason}"
    )
    assert artifact.case_id == "CASE-BACKEND-001"

    # --- 缺 case_id → vet 通过后 distill_skill 抛 ValueError ---------------
    with pytest.raises(ValueError, match="case_id is required"):
        guard.vet_distill(mgr, **_BACKEND_SKILL)

    # --- stat: 临时子库有文件，真库 mtime 未变 ---------------------------------
    sublib = (tmp_path / "backend_lib").resolve()
    assert sublib.exists(), "backend sublib 必须由 SkillMemoryManager 创建"

    skill_index = sublib / "skill_index.jsonl"
    assert skill_index.exists(), "skill_index.jsonl 必须写入 backend sublib"
    assert skill_index.stat().st_mtime > 0

    audit_log = sublib / "guard_audit.jsonl"
    assert audit_log.exists(), "guard_audit.jsonl 必须由 SkillVetting 写入"

    if real_mtime_before is not None:
        real_mtime_after = _REAL_MEMORY_ROOT.stat().st_mtime
        assert real_mtime_after == real_mtime_before, (
            f"真库 mtime 被改动！before={real_mtime_before}, after={real_mtime_after}"
        )
