from __future__ import annotations

from pathlib import Path

from competition.services.case_service import CaseCatalog
from competition.services.verification_service import VerificationService


ROOT = Path(__file__).resolve().parents[2]


def test_scope_gate_rejects_interface_change(tmp_path):
    case = CaseCatalog(ROOT).get("demo_counter")
    candidate = case.guided_candidate_source().replace(
        "module first_counter(", "module changed_counter(", 1
    )
    result = VerificationService(ROOT, tmp_path).verify("demo_counter", candidate, run_id="bad-interface")
    scope = next(row for row in result["stages"] if row["name"] == "scope")
    assert scope["status"] == "fail"
    assert result["accepted"] is False


def test_gate_names_are_not_formal_or_regression():
    assert VerificationService.STAGES == (
        "parse", "scope", "compile", "simulation", "oracle",
        "structural_check", "repeatability",
    )
