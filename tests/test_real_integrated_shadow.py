from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest

from r3e.pilot.real_integrated_shadow import run_real_integrated_shadow
from r3e.arena.real_grounded_adapter import (
    RealGroundedArenaAdapter,
    RealGroundedArenaAdapterViolation,
)
from r3e.arena.audit import verify_frozen_round
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)
from r3e.protocol.hashing import hash_file


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
            assert "clean_rtl" not in prompt
            assert isinstance(prompt.get("clean_rtl_hash"), str)
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

        round_dir = tmp_path / "shadow" / "rounds" / round_id
        assert (round_dir / "round_audit.json").is_file()
        assert (round_dir / "verified_episodes.json").is_file()
        challenge = json.loads(
            (round_dir / "blue_challenge_results.jsonl")
            .read_text(encoding="utf-8").splitlines()[0]
        )
        assert challenge["repair_attempts"] == 1
        blue_result = challenge["blue_results"][0]
        assert len(blue_result["candidate_provider_receipts"]) == 3
        selection = blue_result["selection_receipt"]
        assert len(selection["candidate_ids"]) == 3
        assert selection["selected_candidate_id"] in {
            *selection["candidate_ids"],
            None,
        }
        assert selection["selection_policy"] in {
            "oracle_then_minimality_v1",
            "first_verified_v1",
        }
        assert blue_result["oracle_ok"] is (
            selection["selected_candidate_id"] is not None
        )
        assert challenge["grounded_authority_bundle"]["formal_proof_triplet"]["poison"]["verdict"] == "counterexample"
        assert (round_dir / "archive_updates.jsonl").is_file()
        assert (round_dir / "covered_archive_updates.jsonl").is_file()
        residual_updates = [
            row for row in (round_dir / "archive_updates.jsonl")
            .read_text(encoding="utf-8").splitlines()
            if row.strip()
        ]
        covered_updates = [
            row for row in (round_dir / "covered_archive_updates.jsonl")
            .read_text(encoding="utf-8").splitlines()
            if row.strip()
        ]
        assert len(residual_updates) + len(covered_updates) == 1
        episode_manifest = json.loads(
            (round_dir / "verified_episodes.json").read_text(
                encoding="utf-8"
            )
        )
        assert episode_manifest["episode_ids"]
        renewed = json.loads(
            (round_dir / "renewed_challenge_binding.json").read_text(
                encoding="utf-8"
            )
        )
        assert renewed["challenged_policy_hash"] == first["summary"]["active_policy_hash"]
        audit = verify_frozen_round(round_dir)
        assert audit["blue_evaluation_authority"] == "candidate_portfolio_v1"
        assert audit["promoted"] is False
        assert audit["active_policy_hash"] == renewed["challenged_policy_hash"]
        assert audit["verified_episode_manifest_hash"]
        registry_path = tmp_path / "shadow" / "policy_registry.json"
        registry_hash_after_first = hash_file(registry_path)

        resumed = run_real_integrated_shadow(
            project_root=ROOT,
            workspace=tmp_path / "shadow",
            round_id=round_id,
            client=_client(calls),
        )
        assert resumed["summary"] == first["summary"]
        assert len(calls) == 4
        assert hash_file(registry_path) == registry_hash_after_first
    finally:
        shutil.rmtree(tmp_path / "shadow", ignore_errors=True)


def test_grounded_adapter_runtime_workspace_boundary():
    """The documented ignored runtime child is valid, broad roots are not."""
    client = _client([])
    RealGroundedArenaAdapter(
        project_root=ROOT,
        manifest_path=ROOT / "configs/pilot/real_integrated_shadow_manifest_v1.jsonl",
        client=client,
        artifact_root=ROOT / "runtime" / "adapter-boundary-test",
        round_id="boundary",
    )
    for path in (ROOT, ROOT.parent):
        with pytest.raises(RealGroundedArenaAdapterViolation):
            RealGroundedArenaAdapter(
                project_root=ROOT,
                manifest_path=ROOT / "configs/pilot/real_integrated_shadow_manifest_v1.jsonl",
                client=client,
                artifact_root=path,
                round_id="boundary",
            )
