from __future__ import annotations

from pathlib import Path

import pytest

from competition.services.case_service import CaseCatalog, CaseError


ROOT = Path(__file__).resolve().parents[2]


def test_catalog_exposes_exactly_three_cases_and_guided_patches():
    catalog = CaseCatalog(ROOT)
    assert [case.case_id for case in catalog.all()] == [
        "demo_counter", "demo_fsm", "demo_shift"
    ]
    for case in catalog.all():
        assert case.guided_candidate_source() != case.source("buggy")
        assert case.guided_candidate_source() != case.source("reference") or case.case_id != "demo_counter"


def test_case_path_escape_is_rejected():
    catalog = CaseCatalog(ROOT)
    with pytest.raises(CaseError):
        catalog.get("demo_counter").repo_path("../secret.v")
