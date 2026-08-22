from __future__ import annotations

from pathlib import Path

from competition.services.case_service import CaseCatalog
from competition.services.repair_service import RepairService


ROOT = Path(__file__).resolve().parents[2]


def test_guided_demo_uses_blue_portfolio_and_minimal_patch(tmp_path):
    result = RepairService(ROOT, tmp_path).generate(
        "demo_counter", mode="demo", run_id="test"
    )
    assert result["schema_version"] == "r3e-aic-repair-proposals-v2"
    assert result["diagnosis"]["failure_descriptor"]["schema_version"] == "r3e-aic-failure-descriptor-v2"
    assert result["diagnosis"]["failure_descriptor"]["authority"]["descriptor"] == "runtime_generated"
    assert [candidate["id"] for candidate in result["candidates"]] == ["C1", "C2", "C3"]
    assert result["candidates"][1]["source_kind"] == "guided_minimal_patch"
    case = CaseCatalog(ROOT).get("demo_counter")
    assert result["candidates"][1]["replacement_rtl"] != case.source("reference")
    assert result["candidates"][1]["scope"]["ok"] is True
    assert result["candidates"][1]["verification"]["accepted"] is True
    assert result["portfolio_evaluation"]["selection_receipt"]["selected_candidate_id"]


def test_guided_demo_is_repeatable_for_all_cases(tmp_path):
    service = RepairService(ROOT, tmp_path)
    for case_id in ("demo_counter", "demo_fsm", "demo_shift"):
        result = service.generate(case_id, mode="demo", run_id=f"test-{case_id}")
        assert [row["id"] for row in result["candidates"]] == ["C1", "C2", "C3"]
        assert [row["verification"]["accepted"] for row in result["candidates"]] == [False, True, False]
