from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

import r3e.pilot.integrated_shadow_admission as integrated
from r3e.pilot.integrated_shadow_admission import (
    build_integrated_shadow_preflight,
    run_integrated_shadow_admission,
    run_safe_integrated_shadow_admission,
)
from r3e.pilot.promotion_readiness import assess_policy_promotion_readiness
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
    OpenAICompatibleEmptyContentViolation,
)
from r3e.protocol.ledger import append_ledger
from r3e.protocol.hashing import hash_payload


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PRESENT = bool(shutil.which("yosys") and shutil.which("iverilog"))


def test_integrated_shadow_preflight_is_secret_free_and_no_network():
    plan = build_integrated_shadow_preflight(project_root=ROOT)
    assert plan["schema_version"] == "r3e-integrated-shadow-preflight-v1"
    assert plan["expected_red_provider_calls"] == 1
    assert plan["expected_blue_provider_calls"] == 12
    assert plan["expected_total_provider_calls"] == 13
    assert plan["grounded_target_choice_request_max_output_tokens"] == 512
    assert plan["blue_repair_request_max_output_tokens"] == 4096
    assert plan["promotion_enabled"] is False
    assert plan["memory_qualification_enabled"] is False
    assert plan["registry_mutation_allowed"] is False
    assert plan["resume_additional_calls"] == 0
    assert plan["network_calls"] == 0
    assert plan["preflight_hash"] == hash_payload({
        key: value for key, value in plan.items() if key != "preflight_hash"
    })


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
                "rationale": "integrated same-poison target",
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
            "provider_request_id": f"integrated-shadow-{len(calls)}",
        }

    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "deepseek",
        "provider_version": "injected-integrated-v1",
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
def test_integrated_shadow_is_one_red_plus_twelve_same_poison_blue_calls(tmp_path):
    calls: list[dict] = []
    workspace = tmp_path / "integrated"
    first = run_integrated_shadow_admission(
        project_root=ROOT,
        workspace=workspace,
        client=_client(calls),
    )
    assert len(calls) == 13
    # Grounded target choice uses the small proposal-only request budget;
    # Blue RTL repair retains the frozen per-cell client budget.
    assert calls[0]["max_tokens"] == 512
    assert all(call["max_tokens"] == 4096 for call in calls[1:])
    assert first["expected_red_provider_calls"] == 1
    assert first["expected_blue_provider_calls"] == 12
    assert first["expected_total_provider_calls"] == 13
    assert first["provider_calls"] == 13
    assert first["call_matched"] is True
    assert first["formal_triplet"] == {
        "clean": "proved",
        "poison": "counterexample",
        "revert": "proved",
    }
    assert first["promotion_executed"] is False
    assert first["memory_qualification_executed"] is False
    assert first["challenged_poison_id"]
    assert first["challenged_poison_payload_hash"].startswith("sha256:")
    assert (workspace / "grounded_red" / "execution" / "candidate.json").is_file()
    event_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            workspace / "grounded_red" / "events.jsonl",
            workspace / "same_poison_blue" / "events.jsonl",
            workspace / "same_poison_blue" / "provider_calls.jsonl",
        )
        if path.is_file()
    )
    for forbidden in ("clean_rtl", "replacement_rtl", str(ROOT)):
        assert forbidden not in event_text
    archive = workspace / "archives" / f"{first['archive_kind']}.jsonl"
    assert archive.is_file()
    assert len([line for line in archive.read_text(encoding="utf-8").splitlines() if line.strip()]) == 1
    episode_manifest = json.loads(
        (workspace / "verified_episodes.json").read_text(encoding="utf-8")
    )
    assert episode_manifest["episode_ids"]
    assert first["archive_kind"] in {"residual", "covered"}
    assert first["verified_episode_manifest_hash"] == episode_manifest["manifest_hash"]
    renewed = json.loads(
        (workspace / "renewed_challenge_binding.json").read_text(encoding="utf-8")
    )
    assert renewed["challenged_policy_hash"] == first["challenged_policy_hash"]
    assert renewed["poison_payload_hash"] == first["challenged_poison_payload_hash"]
    assert first["renewed_challenge_hash"] == renewed["binding_hash"]
    assert first["registry_hash_before"] == first["registry_hash_after"]

    # The policy-transition gate must consume the new integrated workspace,
    # not silently downgrade it to the historical independent-lane format.
    target = tmp_path / "target.jsonl"
    target.write_text(
        '{"case_id":"c0","design":"d0"}\n'
        '{"case_id":"c1","design":"d1"}\n',
        encoding="utf-8",
    )
    non_target = tmp_path / "non_target.jsonl"
    non_target.write_text('{"case_id":"n0","design":"n0"}\n', encoding="utf-8")
    registry_before = tmp_path / "registry-before.json"
    registry_after = tmp_path / "registry-after.json"
    registry_before.write_text('{"registry_hash":"same"}\n', encoding="utf-8")
    registry_after.write_text('{"registry_hash":"same"}\n', encoding="utf-8")
    from r3e.protocol.hashing import hash_file
    binding = {
        "schema_version": "r3e-real-policy-promotion-rehearsal-binding-v1",
        "shadow_mode": "integrated_same_poison",
        "execution_mode": "real_provider",
        "round_ids": ["R000", "R001"],
        "challenge_seeds": [17],
        "promotion_seeds": [17],
        "policy_promotion_enabled": True,
        "memory_promotion_enabled": False,
        "memory_qualification_enabled": False,
        "at_most_one_registry_commit": True,
        "model_binding": {"provider_id": "p", "model_id": "m", "model_version": "1"},
        "toolchain_fingerprint_hash": "sha256:" + "a" * 64,
        "budget_binding": {
            "maximum_llm_calls_per_case": 3,
            "maximum_input_tokens_per_case": 2048,
            "maximum_output_tokens_per_case": 4096,
            "maximum_wall_time_ms_per_case": 60000,
        },
        "promotion_thresholds": {"min_target_recovery_ratio": 0.5},
        "target_manifest": {"path": "target.jsonl", "file_hash": hash_file(target)},
        "non_target_manifest": {"path": "non_target.jsonl", "file_hash": hash_file(non_target)},
        "registry_snapshots": {
            "before": {"path": "registry-before.json", "file_hash": hash_file(registry_before)},
            "after": {"path": "registry-after.json", "file_hash": hash_file(registry_after)},
        },
    }
    readiness = assess_policy_promotion_readiness(
        shadow_workspace=workspace,
        rehearsal_binding=binding,
        project_root=tmp_path,
    )
    assert readiness["ready"] is True
    assert readiness["blockers"] == []

    resumed = run_integrated_shadow_admission(
        project_root=ROOT,
        workspace=workspace,
        client=_client(calls),
    )
    assert resumed == first
    assert len(calls) == 13


def test_integrated_shadow_failure_is_terminal_and_cannot_resume(
    tmp_path, monkeypatch
):
    calls: list[dict] = []

    def failing_admission(**kwargs):
        calls.append(kwargs)
        output = Path(kwargs["workspace"])
        append_ledger(
            output / "grounded_red" / "execution" / "provider_calls.jsonl",
            {
                "schema_version": "r3e-grounded-red-provider-call-v1",
                "matrix_id": "test-matrix",
                "matrix_hash": "sha256:" + "0" * 64,
                "case_id": "public-red-case",
                "seed": 17,
                "arm_id": "grounded_red",
                "event_type": "provider_call_started",
                "assignment_id": "GRD6-0001",
                "intent_id": "intent-0",
            },
        )
        raise OpenAICompatibleEmptyContentViolation(
            "provider response must not persist",
            diagnostics={
                "schema_version": (
                    "r3e-openai-compatible-response-diagnostic-v1"
                ),
                "content_state": "blank_content",
                "finish_reason": "length",
                "refusal_present": False,
                "provider_request_id_present": True,
                "input_tokens": 41,
                "output_tokens": 0,
                "prompt": "must not persist",
            },
        )

    monkeypatch.setattr(integrated, "run_integrated_shadow_admission", failing_admission)
    workspace = tmp_path / "terminal-integrated"
    with pytest.raises(
        integrated.IntegratedShadowAdmissionViolation,
        match="terminally checkpointed",
    ):
        run_safe_integrated_shadow_admission(
            project_root=ROOT,
            workspace=workspace,
            client=object(),
        )
    failure_path = workspace / "terminal_failure.json"
    payload = json.loads(failure_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == integrated.INTEGRATED_FAILURE_SCHEMA
    assert payload["failed_stage"] == "grounded_red"
    assert payload["provider_calls_consumed"] == 1
    assert payload["terminal"] is True
    assert payload["provider_diagnostics"]["content_state"] == "blank_content"
    assert payload["failure_hash"] == hash_payload({
        key: value for key, value in payload.items() if key != "failure_hash"
    })
    text = failure_path.read_text(encoding="utf-8")
    assert "provider response must not persist" not in text
    assert "must not persist" not in text
    assert str(ROOT) not in text

    with pytest.raises(
        integrated.IntegratedShadowAdmissionViolation,
        match="already recorded",
    ):
        run_safe_integrated_shadow_admission(
            project_root=ROOT,
            workspace=workspace,
            client=object(),
        )
    assert len(calls) == 1
