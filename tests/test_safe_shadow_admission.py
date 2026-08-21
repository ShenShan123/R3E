from __future__ import annotations

import json
from pathlib import Path

import pytest

import r3e.pilot.safe_shadow_admission as safe
from r3e.pilot.shadow_admission import ShadowAdmissionViolation
from r3e.protocol.hashing import hash_payload
from r3e.protocol.ledger import append_ledger


ROOT = Path(__file__).resolve().parents[1]


def test_provider_failure_is_terminal_and_resume_cannot_recall(tmp_path, monkeypatch):
    calls = []

    def fake_admission(**kwargs):
        calls.append(kwargs)
        output = Path(kwargs["workspace"])
        (output / "blue_matrix").mkdir(parents=True)
        (output / "blue_matrix" / "summary.json").write_text(
            json.dumps({"completed_cells": 4}), encoding="utf-8"
        )
        raise RuntimeError("provider failure text must not be persisted")

    monkeypatch.setattr(safe, "run_shadow_admission", fake_admission)
    workspace = tmp_path / "terminal"
    with pytest.raises(ShadowAdmissionViolation, match="terminally checkpointed"):
        safe.run_safe_shadow_admission(
            project_root=ROOT,
            workspace=workspace,
            client=object(),
        )
    failure_path = workspace / "terminal_failure.json"
    payload = json.loads(failure_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == safe.FAILURE_SCHEMA
    assert payload["failed_stage"] == "grounded_red"
    assert payload["provider_calls_consumed"] == 1
    assert payload["terminal"] is True
    assert "provider failure text" not in failure_path.read_text()
    assert str(ROOT) not in failure_path.read_text()
    assert payload["failure_hash"] == hash_payload({
        key: value for key, value in payload.items() if key != "failure_hash"
    })

    with pytest.raises(ShadowAdmissionViolation, match="already recorded"):
        safe.run_safe_shadow_admission(
            project_root=ROOT,
            workspace=workspace,
            client=object(),
        )
    assert len(calls) == 1


def test_terminal_failure_counts_started_blue_calls(tmp_path, monkeypatch):
    def fake_admission(**kwargs):
        output = Path(kwargs["workspace"])
        for index in range(7):
            append_ledger(
                output / "blue_matrix" / "provider_calls.jsonl",
                {
                    "schema_version": "r3e-shadow-provider-call-v1",
                    "matrix_id": "test-matrix",
                    "matrix_hash": "sha256:" + "0" * 64,
                    "case_id": "public-case",
                    "seed": 17,
                    "arm_id": "C",
                    "event_type": "provider_call_started",
                    "candidate_id": f"C_{index}",
                    "slot_index": index,
                    "candidate_seed": index,
                },
            )
        raise RuntimeError("provider response text must not persist")

    monkeypatch.setattr(safe, "run_shadow_admission", fake_admission)
    workspace = tmp_path / "terminal-blue"
    with pytest.raises(ShadowAdmissionViolation, match="terminally checkpointed"):
        safe.run_safe_shadow_admission(
            project_root=ROOT,
            workspace=workspace,
            client=object(),
        )
    payload = json.loads((workspace / "terminal_failure.json").read_text())
    assert payload["failed_stage"] == "blue_matrix"
    assert payload["provider_calls_consumed"] == 7
    assert "provider response text" not in (
        workspace / "terminal_failure.json"
    ).read_text()
