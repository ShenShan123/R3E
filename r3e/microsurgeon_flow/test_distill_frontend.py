"""
pytest — distill_frontend: build_frontend_skill_payload + distill_frontend_skill 单测.

全程 tmp_path 临时库；.micro_surgeon_memory/frontend_lib 零触碰。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from distill_frontend import build_frontend_skill_payload, distill_frontend_skill
from three_lib_gate import _REAL_MEMORY_ROOT, make_domain_io

_REAL_FRONTEND_INDEX = _REAL_MEMORY_ROOT / "frontend_lib" / "skill_index.jsonl"

# ── Shared mock RepairResults ─────────────────────────────────────────────────

_SUCCESS_RESULT = SimpleNamespace(
    success=True,
    patch={
        "file": "i2c_master_axil.v",
        "line": 597,
        "old_identifier": "s_axil_read_pending",
        "new_identifier": "s_axil_rvalid",
    },
    rationale="mock rationale",
    model="deepseek-v4-flash",       # aligned with golden sample / real API response
    proven=191,
    unproven=0,
    total=191,
    asserted_ok=True,
    yosys_exit=0,
    equiv_log_path="/tmp/fake_equiv.log",
    repaired_rtl_path="/tmp/fake/i2c_master_axil.v",
    old_identifier="s_axil_read_pending",
    target_line=597,
    new_identifier="s_axil_rvalid",
)

_FAILED_RESULT = SimpleNamespace(
    success=False,
    patch={},
    rationale="",
    model="",
    proven=150,
    unproven=41,
    total=191,
    asserted_ok=False,
    yosys_exit=1,
    equiv_log_path="",
    repaired_rtl_path="",
    old_identifier="s_axil_read_pending",
    target_line=597,
    new_identifier="",
)

_COMMON_KWARGS = dict(
    case_id="CASE-FRONTEND-i2c-l597",
    error_signature="Unable to bind wire/reg/memory s_axil_read_pending",
    context_pattern="axil read channel, undeclared identifier at s_axil_read_pending",
    skill_name="frontend_undeclared_identifier_llm_single_site_fix_i2c_l597",
    rtl_rel_path="rtl/i2c_master_axil.v",
)


# ── 1. build_frontend_skill_payload ──────────────────────────────────────────

class TestBuildFrontendSkillPayload:

    def _build(self, result=_SUCCESS_RESULT, **overrides):
        return build_frontend_skill_payload(result, **{**_COMMON_KWARGS, **overrides})

    def test_top_level_keys_present(self):
        p = self._build()
        for k in ("skill_name", "precondition", "action_template",
                  "validation", "rollback_condition"):
            assert k in p, f"missing top-level key: {k!r}"

    def test_skill_name(self):
        assert self._build()["skill_name"] == _COMMON_KWARGS["skill_name"]

    def test_precondition_fields(self):
        pre = self._build()["precondition"]
        assert pre["error_signature"] == _COMMON_KWARGS["error_signature"]
        assert pre["context_pattern"] == _COMMON_KWARGS["context_pattern"]

    def test_allowed_edit_scope(self):
        scope = self._build()["action_template"]["allowed_edit_scope"]
        assert scope == ["rtl/i2c_master_axil.v:L597"]

    def test_key_actions_contains_arrow(self):
        ka = self._build()["action_template"]["key_actions"]
        assert len(ka) == 1
        assert "-> s_axil_rvalid" in ka[0], f"key_action: {ka[0]!r}"

    def test_repair_strategy(self):
        at = self._build()["action_template"]
        assert at["repair_strategy"] == "llm_single_site_identifier_replacement"

    def test_source_model(self):
        assert self._build()["action_template"]["source_model"] == "deepseek-v4-flash"

    def test_validation_frontend_metric(self):
        assert self._build()["validation"]["frontend_metric"] == "equiv_induct_proven"

    def test_validation_equiv_result_filled(self):
        assert self._build()["validation"]["equiv_result"] == "191/191 proven, 0 unproven"

    def test_validation_equiv_config(self):
        assert self._build()["validation"]["equiv_config"] == "equiv_induct -undef -seq 4"

    def test_rollback_condition(self):
        rc = self._build()["rollback_condition"]
        assert rc["equiv_fails_or_frontend_regression"] is True

    def test_no_case_id_in_payload(self):
        """case_id must NOT be in payload — passed separately to vet_distill."""
        assert "case_id" not in self._build()

    def test_no_backend_timing_in_validation(self):
        """A3: backend timing fields must be absent from frontend validation."""
        banned = {"backend_metric", "final_wns", "final_tns",
                  "final_area", "setup_violations"}
        v = self._build()["validation"]
        assert not (banned & set(v.keys())), (
            f"backend timing fields leaked into validation: {banned & set(v.keys())}"
        )

    def test_no_timing_delta_in_action(self):
        """A3: timing delta fields must be absent from frontend action_template."""
        banned = {"delta_wns", "delta_tns", "delta_area"}
        at = self._build()["action_template"]
        assert not (banned & set(at.keys()))

    def test_failed_result_raises_value_error(self):
        with pytest.raises(ValueError, match="success=False"):
            build_frontend_skill_payload(_FAILED_RESULT, **_COMMON_KWARGS)


# ── 2. distill_frontend_skill — end-to-end with tmp_path ────────────────────

class TestDistillFrontendSkill:

    def test_e2e_distill_tmp_lib(self, monkeypatch, tmp_path):
        """Full distill pipeline into tmp_path — happened=True, skill written."""
        monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
        monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)

        happened, vet_result, artifact = distill_frontend_skill(
            _SUCCESS_RESULT, **_COMMON_KWARGS
        )

        assert happened is True, (
            f"vet vetoed: code={vet_result.veto_code}  reason={vet_result.reason}"
        )
        assert artifact is not None
        assert artifact.case_id == _COMMON_KWARGS["case_id"]

        skill_index = tmp_path / "frontend_lib" / "skill_index.jsonl"
        assert skill_index.exists(), "skill_index.jsonl not written to tmp frontend_lib"
        lines = [ln.strip() for ln in skill_index.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1, f"expected 1 entry, got {len(lines)}"

        entry = json.loads(lines[0])
        assert entry["skill_name"] == _COMMON_KWARGS["skill_name"]
        assert entry["case_id"] == _COMMON_KWARGS["case_id"]
        assert entry["validation"]["frontend_metric"] == "equiv_induct_proven"
        assert entry["validation"]["equiv_result"] == "191/191 proven, 0 unproven"
        assert "precondition" in entry
        assert "action_template" in entry

    def test_real_library_untouched(self, monkeypatch, tmp_path):
        """Distill into tmp_path; verify real frontend_lib mtime/size unchanged."""
        real_mtime_before = (
            _REAL_FRONTEND_INDEX.stat().st_mtime
            if _REAL_FRONTEND_INDEX.exists() else None
        )
        real_size_before = (
            _REAL_FRONTEND_INDEX.stat().st_size
            if _REAL_FRONTEND_INDEX.exists() else None
        )

        monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
        monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)
        distill_frontend_skill(_SUCCESS_RESULT, **_COMMON_KWARGS)

        if real_mtime_before is not None:
            after = _REAL_FRONTEND_INDEX.stat()
            assert after.st_mtime == real_mtime_before, (
                f"真库 mtime 被改动! before={real_mtime_before}, after={after.st_mtime}"
            )
            assert after.st_size == real_size_before, (
                f"真库 size 被改动! before={real_size_before}, after={after.st_size}"
            )

    # ── A3 negative: backend timing field in frontend validation → VETO ──────

    def test_a3_backend_timing_in_frontend_validation_vetoed(self, monkeypatch, tmp_path):
        """A3: final_wns in frontend validation → SCHEMA_VIOLATION veto."""
        monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
        monkeypatch.delenv("ALLOW_REAL_MEMORY_WRITE", raising=False)

        mgr, guard = make_domain_io("frontend")

        bad_payload = dict(
            skill_name="bad_frontend_skill",
            precondition={"error_signature": "test_sig", "context_pattern": "test_pat"},
            action_template={
                "repair_strategy": "llm_single_site_identifier_replacement",
                "allowed_edit_scope": ["rtl/test.v:L1"],
                "key_actions": ["replace a -> b"],
                "source_model": "mock",
            },
            validation={
                "frontend_metric": "equiv_induct_proven",
                "final_wns": -0.1,          # ← A3 backend/timing field
                "equiv_result": "10/10 proven, 0 unproven",
                "equiv_config": "equiv_induct -undef -seq 4",
            },
            rollback_condition={"equiv_fails_or_frontend_regression": True},
        )
        happened, vet_result, artifact = guard.vet_distill(
            mgr, **bad_payload, case_id="CASE-A3-NEG"
        )
        assert happened is False, "A3 should veto final_wns in frontend validation"
        assert vet_result.veto_code == "SCHEMA_VIOLATION", (
            f"expected SCHEMA_VIOLATION, got {vet_result.veto_code!r}"
        )
