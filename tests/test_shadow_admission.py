from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from r3e.pilot.shadow_admission import (
    ShadowAdmissionViolation,
    run_shadow_admission,
)
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PRESENT = bool(shutil.which("yosys") and shutil.which("iverilog"))


def _client(transport):
    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "deepseek",
        "provider_version": "injected-admission-v1",
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
    return OpenAICompatibleJSONClient(config, transport=transport, environ={})


@pytest.mark.skipif(not TOOLS_PRESENT, reason="Yosys/Icarus are required")
def test_shadow_admission_is_exactly_twelve_plus_one_and_resumable(tmp_path):
    row = next(
        json.loads(line)
        for line in (ROOT / "datasets/manifests/strider14.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("case_id") == "strider:mux_4_1_1"
    )
    golden = (ROOT / row["golden_rtl"]).read_text(encoding="utf-8")
    calls = []

    def transport(**request):
        calls.append(request)
        prompt = json.loads(request["messages"][1]["content"])
        if "allowed_nodes" in prompt:
            content = {
                "target_module": prompt["target_module"],
                "node_ordinal": prompt["allowed_nodes"][0]["node_ordinal"],
                "rationale": "deterministic manifest target",
            }
        else:
            content = {
                "replacement_rtl": golden,
                "edit": "restore public golden behavior",
            }
        return {
            "content": json.dumps(content),
            "input_tokens": 100,
            # Exercise the real Grounded Red budget boundary: this is above
            # the former 1024-token assignment but below the frozen 4096
            # shadow ceiling.
            "output_tokens": 1500 if "allowed_nodes" in prompt else 20,
            "provider_request_id": f"injected-admission-{len(calls)}",
        }

    workspace = tmp_path / "admission"
    try:
        first = run_shadow_admission(
            project_root=ROOT,
            workspace=workspace,
            client=_client(transport),
            smoke_only=True,
        )
        assert first["expected_blue_provider_calls"] == 12
        assert first["expected_red_provider_calls"] == 1
        assert first["expected_total_provider_calls"] == 13
        assert first["blue_call_matched"] is True
        assert first["promotion_executed"] is False
        assert first["memory_qualification_executed"] is False
        assert first["red_status"] == "admitted"
        assert len(calls) == 13
        red_ledger = [
            json.loads(line)
            for line in (
                workspace / "grounded_red" / "execution" / "provider_calls.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [row["event_type"] for row in red_ledger] == [
            "provider_call_started",
            "provider_call_completed",
        ]
        red_ledger_text = json.dumps(red_ledger)
        for forbidden in (
            "clean_rtl",
            "replacement_rtl",
            "prompt_asset",
            str(ROOT),
        ):
            assert forbidden not in red_ledger_text

        resumed = run_shadow_admission(
            project_root=ROOT,
            workspace=workspace,
            client=_client(transport),
            smoke_only=True,
        )
        assert resumed["summary_hash"] == first["summary_hash"]
        assert len(calls) == 13
        missing_cell = next(
            workspace.glob("blue_matrix/cells/**/cell_summary.json")
        )
        missing_cell.unlink()
        with pytest.raises(
            ShadowAdmissionViolation,
            match="incomplete blue cell",
        ):
            run_shadow_admission(
                project_root=ROOT,
                workspace=workspace,
                client=_client(transport),
                smoke_only=True,
            )
        assert len(calls) == 13
        summary_path = workspace / "summary.json"
        tampered = json.loads(summary_path.read_text(encoding="utf-8"))
        tampered["expected_total_provider_calls"] = 999
        summary_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ShadowAdmissionViolation, match="summary hash"):
            run_shadow_admission(
                project_root=ROOT,
                workspace=workspace,
                client=_client(transport),
                smoke_only=True,
            )
        assert len(calls) == 13
        summary_text = (workspace / "summary.json").read_text(encoding="utf-8")
        assert "replacement_rtl" not in summary_text
        assert str(ROOT) not in summary_text
    finally:
        shutil.rmtree(ROOT / "runtime/real_integrated_shadow", ignore_errors=True)
