from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest

from r3e.pilot.real_integrated_shadow import run_real_integrated_shadow
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
                "rationale": "select the first parser-authorized comparator",
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
            "provider_request_id": f"injected-shadow-{len(calls)}",
        }

    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "injected-shadow",
        "provider_version": "1",
        "endpoint_id": "local-test",
        "model_id": "injected-shadow-model",
        "model_version": "1",
        "api_key_env": "R3E_TEST_API_KEY",
        "base_url_env": "R3E_TEST_BASE_URL",
        "timeout_seconds": 30,
        "maximum_output_tokens": 4096,
        "temperature": 0,
        "require_seed": True,
    })
    return OpenAICompatibleJSONClient(config, transport=transport, environ={})


@pytest.mark.skipif(not TOOLS_PRESENT, reason="Icarus and Yosys are required")
def test_real_integrated_shadow_is_runner_owned_and_resumable(tmp_path):
    calls: list[dict] = []
    round_id = f"R_TEST_{uuid.uuid4().hex[:10]}"
    try:
        first = run_real_integrated_shadow(
            project_root=ROOT,
            workspace=tmp_path / "shadow",
            round_id=round_id,
            client=_client(calls),
        )
        assert first["promotion_executed"] is False
        assert first["memory_qualification_executed"] is False
        assert first["summary"]["promoted"] is False
        # One Grounded target-choice request plus one ACP request per slot.
        assert len(calls) == 4

        round_dir = ROOT / "runtime/real_integrated_shadow" / round_id
        assert (round_dir / "round_audit.json").is_file()
        assert (round_dir / "verified_episodes.json").is_file()
        challenge = json.loads(
            (round_dir / "blue_challenge_results.jsonl")
            .read_text(encoding="utf-8").splitlines()[0]
        )
        assert challenge["repair_attempts"] == 1
        assert len(challenge["blue_results"][0]["candidate_provider_receipts"]) == 3
        assert challenge["grounded_authority_bundle"]["formal_proof_triplet"]["poison"]["verdict"] == "counterexample"

        resumed = run_real_integrated_shadow(
            project_root=ROOT,
            workspace=tmp_path / "shadow",
            round_id=round_id,
            client=_client(calls),
        )
        assert resumed["summary"] == first["summary"]
        assert len(calls) == 4
    finally:
        shutil.rmtree(ROOT / "runtime/real_integrated_shadow" / round_id, ignore_errors=True)
        shutil.rmtree(ROOT / "runtime/real_integrated_shadow" / "artifacts" / round_id, ignore_errors=True)
