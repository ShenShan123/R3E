from __future__ import annotations

from pathlib import Path

from competition.services.case_service import CaseCatalog
from competition.services.verification_service import (
    CompetitionCandidateVerifier,
    VerificationService,
)


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


def test_candidate_verifier_selection_signal_requires_all_gates():
    class FakeService:
        def verify(self, case_id, replacement, *, run_id):
            assert case_id == "demo_counter"
            assert replacement == "module first_counter; endmodule"
            assert run_id == "audit-C0"
            names = VerificationService.STAGES
            stages = []
            for name in names:
                evidence = {"ok": True} if name == "scope" else {}
                stages.append({"name": name, "status": "pass", "evidence": evidence})
            stages[-1]["status"] = "fail"  # repeatability must veto selection
            return {
                "accepted": False,
                "stages": stages,
                "core_oracle": {"ok": True},
            }

    output = CompetitionCandidateVerifier(FakeService(), "audit")(
        policy={},
        case={"case_id": "demo_counter"},
        current_case_evidence={},
        slot={},
        candidate_id="C0",
        patch_payload={"replacement_rtl": "module first_counter; endmodule"},
    )
    assert output["oracle_ok"] is False
