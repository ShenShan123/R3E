from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from r3e.pilot.grd8_acp7_smoke import (
    _load_manifest_case,
    _run_acp7,
    _run_grd8,
)
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)


ROOT = Path(__file__).resolve().parents[1]
HAS_AUTHORITY_TOOLS = all(
    shutil.which(name) for name in ("iverilog", "vvp", "yosys")
)


def _injected_client(calls, *, invalid_acp_slot=None):
    reference = (
        ROOT
        / "datasets/cases/strider14/mux_4_1/mux_4_1.v"
    ).read_text(encoding="utf-8")

    acp_calls = 0

    def transport(**request):
        nonlocal acp_calls
        calls.append(request)
        user = json.loads(request["messages"][1]["content"])
        if "allowed_nodes" in user:
            result = {
                "target_module": "m",
                "node_ordinal": 0,
                "rationale": (
                    "exercise the first parser-authorized comparator"
                ),
            }
        else:
            replacement = reference
            if acp_calls == invalid_acp_slot:
                replacement = "module broken("
            result = {
                "replacement_rtl": replacement,
                "edit": "restore selector case labels",
            }
            acp_calls += 1
        return {
            "content": json.dumps(result),
            "input_tokens": 100,
            "output_tokens": 50,
            "provider_request_id": f"injected-{len(calls)}",
        }

    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "injected-pilot",
        "provider_version": "1",
        "endpoint_id": "local-test",
        "model_id": "injected-model",
        "model_version": "1",
        "api_key_env": "R3E_TEST_API_KEY",
        "base_url_env": "R3E_TEST_BASE_URL",
        "timeout_seconds": 10,
        "maximum_output_tokens": 4096,
        "temperature": 0,
        "require_seed": True,
    })
    return OpenAICompatibleJSONClient(
        config, transport=transport, environ={}
    )


@pytest.mark.skipif(
    not HAS_AUTHORITY_TOOLS,
    reason="Icarus or Yosys is unavailable",
)
def test_injected_grd8_acp7_smoke_closes_all_runner_owned_gates(
    tmp_path,
):
    calls = []
    client = _injected_client(calls)
    manifest_row = _load_manifest_case(
        ROOT / "datasets/manifests/strider14.jsonl",
        "strider:mux_4_1_1",
    )
    grd8 = _run_grd8(
        root=ROOT,
        workspace=tmp_path / "grd8",
        client=client,
        seed=17,
    )
    acp7 = _run_acp7(
        root=ROOT,
        workspace=tmp_path / "acp7",
        client=client,
        manifest_row=manifest_row,
        seed=17,
    )
    assert len(calls) == 4
    assert grd8["status"] == "passed"
    assert grd8["formal_triplet"] == {
        "clean": "proved",
        "poison": "counterexample",
        "revert": "proved",
    }
    assert acp7["status"] == "passed"
    assert acp7["provider_calls"] == 3
    assert acp7["verified_candidates"] == 3
    assert acp7["oracle_ok"]
    evaluation = json.loads(
        (tmp_path / "acp7/blue_evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(
        "semantic_patch" not in receipt["patch_payload"]
        for receipt in evaluation["candidate_provider_receipts"]
    )
    for receipt in evaluation[
        "candidate_semantic_signature_receipts"
    ]:
        signature = receipt["signature"]
        assert signature["changed_modules"] == ["mux_4to1_case"]
        assert signature["changed_blocks"] == [
            "module:mux_4to1_case/case:0"
        ]
        assert signature["operator_classes"] == [
            "constant_replacement"
        ]


@pytest.mark.skipif(
    not HAS_AUTHORITY_TOOLS,
    reason="Icarus or Yosys is unavailable",
)
def test_invalid_ast_candidate_is_rejected_without_aborting_portfolio(
    tmp_path,
):
    calls = []
    client = _injected_client(calls, invalid_acp_slot=0)
    manifest_row = _load_manifest_case(
        ROOT / "datasets/manifests/strider14.jsonl",
        "strider:mux_4_1_1",
    )
    acp7 = _run_acp7(
        root=ROOT,
        workspace=tmp_path / "acp7",
        client=client,
        manifest_row=manifest_row,
        seed=17,
    )
    assert len(calls) == 3
    assert acp7["provider_calls"] == 3
    assert acp7["verified_candidates"] == 2
    assert acp7["oracle_ok"]
    evaluation = json.loads(
        (tmp_path / "acp7/blue_evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    rejected, *accepted = evaluation[
        "candidate_verification_receipts"
    ]
    assert rejected["parse_ok"] is False
    assert rejected["scope_ok"] is False
    assert rejected["oracle_ok"] is False
    assert all(row["oracle_ok"] for row in accepted)
    rejection_signature = evaluation[
        "candidate_semantic_signature_receipts"
    ][0]["signature"]
    assert rejection_signature["operator_classes"] == [
        "ast_materialization_rejected"
    ]
