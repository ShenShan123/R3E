"""
pytest — backend_oneshot: CSR clean baseline 接线贯通单测.

A1 四条: dry_run 不触 ORFS 文件系统，覆盖 payload 结构 + mem_root 注入.
orfs 七条: 需 RUN_ORFS=1，读真实产物 + 全量蒸馏，mem_root → tmp_path 零污染.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest
from backend_oneshot import DESIGN, PLATFORM, run_backend_oneshot

# ── A1 四条 (默认集, 不触 ORFS) ───────────────────────────────────────────────


def test_dry_run_required_keys(tmp_path):
    result = run_backend_oneshot(orfs_root=tmp_path, dry_run=True)
    for k in ("skill_name", "precondition", "action_template",
              "validation", "rollback_condition", "case_id"):
        assert k in result, f"missing key: {k!r}"


def test_dry_run_precondition_fields(tmp_path):
    result = run_backend_oneshot(orfs_root=tmp_path, dry_run=True)
    pre = result["precondition"]
    assert pre["design"] == DESIGN
    assert pre["platform"] == PLATFORM
    assert pre["is_repair_increment"] is False


def test_dry_run_tristate_valid(tmp_path):
    result = run_backend_oneshot(orfs_root=tmp_path, dry_run=True)
    assert result["action_template"]["tristate"] in (
        "closed", "routed_unclosed", "failed"
    )


def test_dry_run_mem_root_injectable(tmp_path):
    """mem_root 注入 tmp_path/iso → dry_run 不创建文件系统写入"""
    result = run_backend_oneshot(
        orfs_root=tmp_path, mem_root=tmp_path / "iso", dry_run=True
    )
    assert "case_id" in result
    assert not (tmp_path / "iso").exists(), "dry_run 不应创建 iso_root 目录"


# ── orfs 七条 (需 RUN_ORFS=1) ─────────────────────────────────────────────────


@pytest.mark.orfs
def test_orfs_route_odb_exists():
    result = run_backend_oneshot(dry_run=True)
    odb = Path(result["action_template"]["route_odb"])
    assert odb.exists(), f"route_odb not found: {odb}"


@pytest.mark.orfs
def test_orfs_gds_exists():
    result = run_backend_oneshot(dry_run=True)
    gds = Path(result["action_template"]["final_gds"])
    assert gds.exists(), f"final_gds not found: {gds}"


@pytest.mark.orfs
def test_orfs_wns_parsed():
    result = run_backend_oneshot(dry_run=True)
    wns = result["action_template"]["setup_wns"]
    assert isinstance(wns, float), f"setup_wns is {type(wns)}, expected float"
    assert wns <= 0.0, f"unexpected positive WNS: {wns}"


@pytest.mark.orfs
def test_orfs_tristate_closed():
    result = run_backend_oneshot(dry_run=True)
    assert result["action_template"]["tristate"] == "closed", (
        f"expected 'closed', got {result['action_template']['tristate']!r}"
    )


@pytest.mark.orfs
def test_orfs_distill_happened(tmp_path):
    result = run_backend_oneshot(mem_root=tmp_path / "iso")
    assert result["_distill_happened"] is True, (
        f"vet vetoed: code={result['_vet_veto_code']}"
    )


@pytest.mark.orfs
def test_orfs_iso_under_mem_root(tmp_path):
    result = run_backend_oneshot(mem_root=tmp_path / "iso")
    assert Path(result["_iso_root"]) == tmp_path / "iso"


@pytest.mark.orfs
def test_orfs_real_lib_untouched(tmp_path):
    from three_lib_gate import _REAL_MEMORY_ROOT
    mtime_before = (
        _REAL_MEMORY_ROOT.stat().st_mtime if _REAL_MEMORY_ROOT.exists() else None
    )
    run_backend_oneshot(mem_root=tmp_path / "iso")
    if mtime_before is not None:
        assert _REAL_MEMORY_ROOT.stat().st_mtime == mtime_before, "真库 mtime 被改动"
