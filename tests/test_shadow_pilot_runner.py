from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from r3e.pilot.grounded_red_shadow import run_grounded_red_shadow
from r3e.pilot.shadow_runner import (
    ShadowPilotRunnerViolation,
    run_shadow_matrix,
)
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)
from r3e.protocol.hashing import hash_file


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PRESENT = bool(shutil.which("yosys") and shutil.which("iverilog"))


def _client(transport):
    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "deepseek",
        "provider_version": "injected-conformance-v1",
        "endpoint_id": "injected-no-network",
        "model_id": "deepseek-v4-pro",
        "model_version": "deepseek-v4-pro",
        "api_key_env": "TEST_API_KEY",
        "base_url_env": "TEST_BASE_URL",
        "timeout_seconds": 30,
        "maximum_output_tokens": 4096,
        "temperature": 0,
        "require_seed": True,
    })
    return OpenAICompatibleJSONClient(
        config, transport=transport, environ={}
    )


def _manifest_row(case_id: str) -> dict:
    return next(
        json.loads(line)
        for line in (ROOT / "datasets/manifests/strider14.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("case_id") == case_id
    )


@pytest.mark.skipif(not TOOLS_PRESENT, reason="Yosys/Icarus are required")
def test_abcd_shadow_smoke_is_call_matched_private_and_resumable(tmp_path):
    row = _manifest_row("strider:mux_4_1_1")
    golden = (ROOT / row["golden_rtl"]).read_text(encoding="utf-8")
    calls = []

    def transport(**request):
        calls.append(request)
        return {
            "content": json.dumps({
                "replacement_rtl": golden,
                "edit": "restore public golden behavior",
            }),
            "input_tokens": 100,
            "output_tokens": 20,
            "provider_request_id": f"injected-blue-{len(calls)}",
        }

    workspace = tmp_path / "shadow"
    first = run_shadow_matrix(
        project_root=ROOT,
        workspace=workspace,
        client=_client(transport),
        smoke_only=True,
    )
    assert first["completed_cells"] == 4
    assert first["call_matched"] is True
    assert len(calls) == 12
    events_text = (workspace / "events.jsonl").read_text(encoding="utf-8")
    assert "replacement_rtl" not in events_text
    assert golden not in events_text
    assert str(ROOT) not in events_text
    aggregate = json.loads(
        (workspace / "aggregate.json").read_text(encoding="utf-8")
    )
    assert set(aggregate["arms"]) == set("ABCD")
    assert all(row["provider_calls"] == 3 for row in aggregate["arms"].values())
    call_ledger = workspace / "provider_calls.jsonl"
    ledger_rows = [
        json.loads(line)
        for line in call_ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert sum(
        row["event_type"] == "provider_call_started" for row in ledger_rows
    ) == 12
    assert "replacement_rtl" not in call_ledger.read_text(encoding="utf-8")
    assert str(ROOT) not in call_ledger.read_text(encoding="utf-8")

    resumed = run_shadow_matrix(
        project_root=ROOT,
        workspace=workspace,
        client=_client(transport),
        smoke_only=True,
    )
    assert resumed["summary_hash"] == first["summary_hash"]
    assert len(calls) == 12

    evaluation = next(workspace.glob("cells/**/blue_evaluation.json"))
    evaluation.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ShadowPilotRunnerViolation, match="hash mismatch"):
        run_shadow_matrix(
            project_root=ROOT,
            workspace=workspace,
            client=_client(transport),
            smoke_only=True,
        )


@pytest.mark.skipif(not TOOLS_PRESENT, reason="Yosys/Icarus are required")
def test_shadow_call_ledger_counts_started_call_on_provider_failure(tmp_path):
    row = _manifest_row("strider:mux_4_1_1")
    golden = (ROOT / row["golden_rtl"]).read_text(encoding="utf-8")
    calls = []

    def transport(**request):
        calls.append(request)
        if len(calls) == 7:
            return {
                "content": "",
                "input_tokens": 100,
                "output_tokens": 0,
                "provider_request_id": "injected-empty-content",
            }
        return {
            "content": json.dumps({
                "replacement_rtl": golden,
                "edit": "restore public golden behavior",
            }),
            "input_tokens": 100,
            "output_tokens": 20,
            "provider_request_id": f"injected-blue-{len(calls)}",
        }

    workspace = tmp_path / "shadow-failure"
    with pytest.raises(Exception, match="provider returned empty content"):
        run_shadow_matrix(
            project_root=ROOT,
            workspace=workspace,
            client=_client(transport),
            smoke_only=True,
        )
    assert len(calls) == 7
    ledger_rows = [
        json.loads(line)
        for line in (workspace / "provider_calls.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert sum(
        row["event_type"] == "provider_call_started" for row in ledger_rows
    ) == 7
    assert any(
        row["event_type"] == "provider_call_failed"
        and row["failure_class"]
        == "OpenAICompatibleEmptyContentViolation"
        for row in ledger_rows
    )
    ledger_text = (workspace / "provider_calls.jsonl").read_text(
        encoding="utf-8"
    )
    assert "provider returned empty content" not in ledger_text
    assert str(ROOT) not in ledger_text


@pytest.mark.skipif(not TOOLS_PRESENT, reason="Yosys/Icarus are required")
def test_grounded_red_shadow_is_one_call_formal_and_resumable(tmp_path):
    calls = []

    def transport(**request):
        calls.append(request)
        prompt = json.loads(request["messages"][1]["content"])
        return {
            "content": json.dumps({
                "target_module": prompt["target_module"],
                "node_ordinal": prompt["allowed_nodes"][0]["node_ordinal"],
                "rationale": "deterministic injected target",
            }),
            "input_tokens": 200,
            "output_tokens": 30,
            "provider_request_id": f"injected-red-{len(calls)}",
        }

    registry = ROOT / "runtime/registry/policy_registry_v2.json"
    before = hash_file(registry) if registry.exists() else "absent"
    workspace = tmp_path / "red"
    first = run_grounded_red_shadow(
        project_root=ROOT,
        workspace=workspace,
        client=_client(transport),
        seed=17,
    )
    assert first["status"] == "admitted"
    assert first["promotion_executed"] is False
    assert first["memory_qualification_executed"] is False
    assert len(calls) == 1
    event = json.loads((workspace / "event.json").read_text(encoding="utf-8"))
    assert event["formal_triplet"] == {
        "clean": "proved",
        "poison": "counterexample",
        "revert": "proved",
    }
    assert event["provider_calls"] == 1

    resumed = run_grounded_red_shadow(
        project_root=ROOT,
        workspace=workspace,
        client=_client(transport),
        seed=17,
    )
    assert resumed["summary_hash"] == first["summary_hash"]
    assert len(calls) == 1
    after = hash_file(registry) if registry.exists() else "absent"
    assert after == before
