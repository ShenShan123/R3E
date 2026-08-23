from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from r3e.pilot.real_integrated_shadow import run_real_integrated_shadow
from r3e.pilot.same_poison_shadow import (
    SamePoisonShadowViolation,
    run_same_poison_blue_portfolio,
)
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PRESENT = bool(shutil.which("yosys") and shutil.which("iverilog"))


def _client(calls: list[dict]) -> OpenAICompatibleJSONClient:
    golden = (ROOT / "configs/pilot/assets/real_integrated_shadow_golden.v").read_text(
        encoding="utf-8"
    )

    def transport(**request):
        calls.append(request)
        prompt = json.loads(request["messages"][1]["content"])
        if "allowed_nodes" in prompt:
            result = {
                "target_module": prompt["target_module"],
                "node_ordinal": prompt["allowed_nodes"][0]["node_ordinal"],
                "rationale": "same-poison conformance target",
            }
        else:
            result = {
                "replacement_rtl": golden,
                "edit": "restore the frozen public golden behavior",
            }
        return {
            "content": json.dumps(result),
            "input_tokens": 100,
            "output_tokens": 20,
            "provider_request_id": f"same-poison-{len(calls)}",
        }

    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "deepseek",
        "provider_version": "injected-same-poison-v1",
        "endpoint_id": "local-test",
        "model_id": "deepseek-v4-pro",
        "model_version": "deepseek-v4-pro",
        "api_key_env": "R3E_TEST_API_KEY",
        "base_url_env": "R3E_TEST_BASE_URL",
        "timeout_seconds": 30,
        "maximum_output_tokens": 4096,
        "temperature": 0,
        "require_seed": True,
    })
    return OpenAICompatibleJSONClient(config, transport=transport, environ={})


@pytest.mark.skipif(not TOOLS_PRESENT, reason="Yosys/Icarus are required")
def test_admitted_red_poison_routes_to_all_four_acp_arms(tmp_path):
    calls: list[dict] = []
    client = _client(calls)
    red_workspace = tmp_path / "red"
    round_id = "R_SAME_POISON"
    run_real_integrated_shadow(
        project_root=ROOT,
        workspace=red_workspace,
        round_id=round_id,
        client=client,
    )
    poison = json.loads(
        (red_workspace / "rounds" / round_id / "valid_poisons.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    before_blue = len(calls)
    first = run_same_poison_blue_portfolio(
        project_root=ROOT,
        workspace=tmp_path / "same-poison",
        client=client,
        poison=poison,
        seed=17,
    )
    assert first["red_provider_calls"] == 1
    assert first["blue_provider_calls"] == 12
    assert first["expected_total_provider_calls"] == 13
    assert first["completed_cells"] == 4
    assert first["call_matched"] is True
    assert first["challenged_poison_id"] == poison["poison_id"]
    assert first["challenged_policy_hash"] == poison["challenged_policy_hash"]
    assert first["formal_triplet"] == {
        "clean": "proved",
        "poison": "counterexample",
        "revert": "proved",
    }
    assert len(calls) - before_blue == 12

    resumed = run_same_poison_blue_portfolio(
        project_root=ROOT,
        workspace=tmp_path / "same-poison",
        client=client,
        poison=poison,
        seed=17,
    )
    assert resumed == first
    assert len(calls) - before_blue == 12

    tampered = dict(poison)
    tampered["poison_id"] = "tampered"
    with pytest.raises(SamePoisonShadowViolation):
        run_same_poison_blue_portfolio(
            project_root=ROOT,
            workspace=tmp_path / "same-poison-tampered",
            client=client,
            poison=tampered,
            seed=17,
        )

