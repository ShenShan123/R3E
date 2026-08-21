from __future__ import annotations

import json
from pathlib import Path

import pytest

from r3e.arena.conformance import AdapterConformanceGate
from r3e.blue.portfolio.openai_provider import (
    OpenAICompatibleCandidateProvider,
    RealCandidateProviderViolation,
)
from r3e.pilot.readiness import assess_pilot_readiness
from r3e.pilot.grd8_acp7_smoke import _client_from_environment
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleEmptyContentViolation,
    OpenAICompatibleJSONClient,
    OpenAICompatibleProviderViolation,
)


ROOT = Path(__file__).resolve().parents[1]


def test_real_pilot_entry_accepts_deepseek_environment_aliases(monkeypatch):
    for name in (
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "injected-test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://injected.invalid/v1")
    client = _client_from_environment()
    assert client.config.model_id == "deepseek-test-model"
    assert client.config.api_key_env == "DEEPSEEK_API_KEY"
    assert client.config.base_url_env == "DEEPSEEK_BASE_URL"
    assert client.config.provider_id == "deepseek"


def test_real_pilot_entry_accepts_llm_environment_template(monkeypatch):
    for name in (
        "OPENAI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "template-test-model")
    monkeypatch.setenv("LLM_API_KEY", "injected-template-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://template.invalid/v1")
    client = _client_from_environment()
    assert client.config.model_id == "template-test-model"
    assert client.config.api_key_env == "LLM_API_KEY"
    assert client.config.base_url_env == "LLM_BASE_URL"


def _config():
    return OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "injected-test-provider",
        "provider_version": "1",
        "endpoint_id": "injected-local-transport",
        "model_id": "test-real-adapter-model",
        "model_version": "frozen-test-version",
        "api_key_env": "R3E_TEST_API_KEY",
        "base_url_env": "R3E_TEST_BASE_URL",
        "timeout_seconds": 10,
        "maximum_output_tokens": 1000,
        "temperature": 0,
        "require_seed": True,
    })


def _policy():
    return PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v3.json"
    ).read_text(encoding="utf-8")))


def _transport(**request):
    assert request["seed"] == 17
    prompt = json.loads(request["messages"][1]["content"])
    assert prompt["output_constraints"][
        "required_top_level_fields"
    ] == ["replacement_rtl", "edit"]
    assert prompt["output_constraints"][
        "semantic_patch_is_runner_owned"
    ] is True
    assert prompt["output_constraints"][
        "echoed_identity_must_match_request"
    ] is True
    return {
        "content": json.dumps({
            "replacement_rtl": (
                "module top(input a, output y); assign y=a; endmodule"
            ),
            "edit": "restore direct assignment",
        }),
        "input_tokens": 91,
        "output_tokens": 37,
        "provider_request_id": "injected-request-1",
    }


def test_real_candidate_provider_is_artifact_bound_and_conformant():
    client = OpenAICompatibleJSONClient(
        _config(), transport=_transport, environ={}
    )
    provider = OpenAICompatibleCandidateProvider(
        client,
        verifier_id="runner-owned-pilot-verifier",
        verifier_version="1",
    )
    source = "module top(input a, output y); assign y=~a; endmodule"
    slot = {
        "slot_index": 0,
        "lens_id": "generic_v1",
        "lens_hash": hash_payload({"lens": "generic_v1"}),
        "candidate_seed": 17,
    }
    result = provider.generate_candidate(
        policy=_policy(),
        current_case_evidence={"first_divergence_signal": "y"},
        current_case_artifact={
            "buggy_rtl_source": source,
            "buggy_rtl_hash": hash_payload(source),
            "top_module": "top",
        },
        slot=slot,
        prompt_asset="repair only the current local block",
        prompt_hash=hash_payload({"prompt": "bound"}),
        candidate_id="C_REAL_0",
    )
    validated = AdapterConformanceGate(provider).validate(
        "generate_blue_candidate", result
    )
    assert validated["input_tokens"] == 91
    assert validated["patch_payload"]["replacement_rtl"].startswith(
        "module top"
    )
    assert "semantic_patch" not in validated["patch_payload"]
    assert validated["current_case_artifact_hash"] == hash_payload({
        "buggy_rtl_source": source,
        "buggy_rtl_hash": hash_payload(source),
        "top_module": "top",
    })


def test_real_candidate_provider_rejects_missing_rtl():
    provider = OpenAICompatibleCandidateProvider(
        OpenAICompatibleJSONClient(
            _config(), transport=_transport, environ={}
        ),
        verifier_id="runner-owned-pilot-verifier",
        verifier_version="1",
    )
    with pytest.raises(
        RealCandidateProviderViolation,
        match="requires current buggy RTL",
    ):
        provider.generate_candidate(
            policy=_policy(),
            current_case_evidence={},
            current_case_artifact={},
            slot={
                "slot_index": 0,
                "lens_id": "generic_v1",
                "lens_hash": hash_payload({"lens": "generic_v1"}),
                "candidate_seed": 17,
            },
            prompt_asset="prompt",
            prompt_hash=hash_payload({"prompt": "bound"}),
            candidate_id="C_REAL_MISSING",
        )


def test_real_candidate_provider_accepts_only_matching_optional_identity():
    def matching_transport(**request):
        raw = _transport(**request)
        content = json.loads(raw["content"])
        content["candidate_id"] = "C_REAL_0"
        content["candidate_seed"] = 17
        return {**raw, "content": json.dumps(content)}

    provider = OpenAICompatibleCandidateProvider(
        OpenAICompatibleJSONClient(
            _config(), transport=matching_transport, environ={}
        ),
        verifier_id="runner-owned-pilot-verifier",
        verifier_version="1",
    )
    source = "module top(input a, output y); assign y=~a; endmodule"
    result = provider.generate_candidate(
        policy=_policy(),
        current_case_evidence={"first_divergence_signal": "y"},
        current_case_artifact={
            "buggy_rtl_source": source,
            "buggy_rtl_hash": hash_payload(source),
            "top_module": "top",
        },
        slot={
            "slot_index": 0,
            "lens_id": "generic_v1",
            "lens_hash": hash_payload({"lens": "generic_v1"}),
            "candidate_seed": 17,
        },
        prompt_asset="repair only the current local block",
        prompt_hash=hash_payload({"prompt": "bound"}),
        candidate_id="C_REAL_0",
    )
    assert result["patch_payload"]["candidate_id"] == "C_REAL_0"

    def mismatched_transport(**request):
        raw = matching_transport(**request)
        content = json.loads(raw["content"])
        content["candidate_id"] = "MODEL_REASSIGNED_ID"
        return {**raw, "content": json.dumps(content)}

    mismatched = OpenAICompatibleCandidateProvider(
        OpenAICompatibleJSONClient(
            _config(), transport=mismatched_transport, environ={}
        ),
        verifier_id="runner-owned-pilot-verifier",
        verifier_version="1",
    )
    with pytest.raises(
        RealCandidateProviderViolation,
        match="identity differs",
    ):
        mismatched.generate_candidate(
            policy=_policy(),
            current_case_evidence={"first_divergence_signal": "y"},
            current_case_artifact={
                "buggy_rtl_source": source,
                "buggy_rtl_hash": hash_payload(source),
                "top_module": "top",
            },
            slot={
                "slot_index": 0,
                "lens_id": "generic_v1",
                "lens_hash": hash_payload({"lens": "generic_v1"}),
                "candidate_seed": 17,
            },
            prompt_asset="repair only the current local block",
            prompt_hash=hash_payload({"prompt": "bound"}),
            candidate_id="C_REAL_0",
        )

    def mismatched_seed_transport(**request):
        raw = matching_transport(**request)
        content = json.loads(raw["content"])
        content["candidate_seed"] = 18
        return {**raw, "content": json.dumps(content)}

    mismatched_seed = OpenAICompatibleCandidateProvider(
        OpenAICompatibleJSONClient(
            _config(), transport=mismatched_seed_transport, environ={}
        ),
        verifier_id="runner-owned-pilot-verifier",
        verifier_version="1",
    )
    with pytest.raises(
        RealCandidateProviderViolation,
        match="seed differs",
    ):
        mismatched_seed.generate_candidate(
            policy=_policy(),
            current_case_evidence={"first_divergence_signal": "y"},
            current_case_artifact={
                "buggy_rtl_source": source,
                "buggy_rtl_hash": hash_payload(source),
                "top_module": "top",
            },
            slot={
                "slot_index": 0,
                "lens_id": "generic_v1",
                "lens_hash": hash_payload({"lens": "generic_v1"}),
                "candidate_seed": 17,
            },
            prompt_asset="repair only the current local block",
            prompt_hash=hash_payload({"prompt": "bound"}),
            candidate_id="C_REAL_0",
        )


def test_real_candidate_provider_rejects_model_semantic_authority():
    def self_report_transport(**request):
        raw = _transport(**request)
        content = json.loads(raw["content"])
        content["semantic_patch"] = {
            "changed_modules": ["top"],
            "patch_scope": "local_block",
        }
        return {**raw, "content": json.dumps(content)}

    provider = OpenAICompatibleCandidateProvider(
        OpenAICompatibleJSONClient(
            _config(), transport=self_report_transport, environ={}
        ),
        verifier_id="runner-owned-pilot-verifier",
        verifier_version="1",
    )
    source = "module top(input a, output y); assign y=~a; endmodule"
    with pytest.raises(
        RealCandidateProviderViolation,
        match="real candidate patch fields mismatch",
    ):
        provider.generate_candidate(
            policy=_policy(),
            current_case_evidence={"first_divergence_signal": "y"},
            current_case_artifact={
                "buggy_rtl_source": source,
                "buggy_rtl_hash": hash_payload(source),
                "top_module": "top",
            },
            slot={
                "slot_index": 0,
                "lens_id": "generic_v1",
                "lens_hash": hash_payload({"lens": "generic_v1"}),
                "candidate_seed": 17,
            },
            prompt_asset="repair only the current local block",
            prompt_hash=hash_payload({"prompt": "bound"}),
            candidate_id="C_REAL_0",
        )


def test_openai_compatible_client_rejects_non_json_without_retry():
    calls = []

    def invalid(**request):
        calls.append(request)
        return {
            "content": "```json\n{}\n```",
            "input_tokens": 1,
            "output_tokens": 1,
            "provider_request_id": "invalid",
        }

    client = OpenAICompatibleJSONClient(
        _config(), transport=invalid, environ={}
    )
    with pytest.raises(
        OpenAICompatibleProviderViolation, match="strict JSON"
    ):
        client.complete_json(
            messages=[{"role": "user", "content": "return JSON"}],
            seed=1,
        )
    assert len(calls) == 1


def test_openai_compatible_client_classifies_empty_content_without_retry():
    calls = []

    def empty(**request):
        calls.append(request)
        return {
            "content": "",
            "input_tokens": 10,
            "output_tokens": 0,
            "provider_request_id": "empty-content",
        }

    client = OpenAICompatibleJSONClient(
        _config(), transport=empty, environ={}
    )
    with pytest.raises(
        OpenAICompatibleEmptyContentViolation, match="empty content"
    ):
        client.complete_json(
            messages=[{"role": "user", "content": "return JSON"}],
            seed=1,
        )
    assert len(calls) == 1


def test_grd8_acp7_template_is_fail_closed_and_secret_free():
    config = json.loads((
        ROOT / "configs/evolution/grd8_acp7_pilot_v1.json"
    ).read_text(encoding="utf-8"))
    readiness = assess_pilot_readiness(
        config, project_root=ROOT, environ={}
    )
    assert readiness["ready"] is False
    assert readiness["executable_requested"] is False
    assert "red_model_id" in readiness["blockers"]
    assert "blue_api_key_env" in readiness["blockers"]
    serialized = json.dumps(config).lower()
    assert "sk-" not in serialized
    assert "api_key\":" not in serialized


def test_readiness_cli_is_secret_free_and_fail_closed(monkeypatch, capsys):
    from r3e.pilot.readiness import _main

    monkeypatch.setattr("sys.argv", [
        "r3e.pilot.readiness",
        "--config",
        str(ROOT / "configs/evolution/grd8_acp7_pilot_v1.json"),
        "--project-root",
        str(ROOT),
    ])
    assert _main() == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is False
    assert output["credential_environment_present"] == {
        "red_api_key_env": False,
        "red_base_url_env": False,
        "blue_api_key_env": False,
        "blue_base_url_env": False,
    }
    assert "api_key" not in output


def test_shadow_admission_readiness_is_narrow_and_call_matched():
    from r3e.pilot.readiness import assess_shadow_admission_readiness

    config = json.loads((
        ROOT / "configs/pilot/shadow_pilot_matrix_v1.json"
    ).read_text(encoding="utf-8"))
    readiness = assess_shadow_admission_readiness(
        config, project_root=ROOT, environ={}
    )
    assert readiness["ready"] is False
    assert readiness["expected_provider_calls"] == 13
    assert readiness["blockers"] == [
        "api_key_env",
        "base_url_env",
        "model_env",
    ]
    assert readiness["formal_toolchain_present"] == {
        "yosys": True,
        "iverilog": True,
        "vvp": True,
    }
    assert "api_key" not in readiness


@pytest.mark.parametrize(
    "name",
    [
        "integrated_deterministic_coevolution_v1.json",
        "real_adapter_pilot_entry_v1.json",
    ],
)
def test_integrated_and_pilot_entry_milestones_are_frozen(name):
    milestone = json.loads((
        ROOT / "configs/evolution" / name
    ).read_text(encoding="utf-8"))
    assert milestone["status"] in {"frozen", "pilot_scaffold_frozen"}
    assert milestone["milestone_hash"] == hash_payload({
        key: value for key, value in milestone.items()
        if key != "milestone_hash"
    })
    for relative, expected in milestone["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected
