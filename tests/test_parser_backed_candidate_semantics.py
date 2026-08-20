from __future__ import annotations

from pathlib import Path

import pytest

from r3e.blue.portfolio.rtl_ast_materializer import (
    AST_SEMANTIC_PATCH_SCHEMA,
    RtlAstMaterializationViolation,
    materialize_rtl_ast_semantic_patch,
)
from r3e.blue.portfolio.semantic_signature import (
    ParserBackedSemanticSignatureProvider,
    SemanticSignatureViolation,
    verify_semantic_signature_receipt,
)
from r3e.protocol.hashing import hash_payload


ROOT = Path(__file__).resolve().parents[1]
BUGGY = (
    ROOT
    / "datasets/cases/strider14/mux_4_1/mux_4_1_wadden_buggy1.v"
)
REPAIRED = ROOT / "datasets/cases/strider14/mux_4_1/mux_4_1.v"


def _sources() -> tuple[str, str]:
    return (
        BUGGY.read_text(encoding="utf-8"),
        REPAIRED.read_text(encoding="utf-8"),
    )


def test_runner_derives_reconstructable_case_label_semantics():
    buggy, repaired = _sources()
    first = materialize_rtl_ast_semantic_patch(
        buggy_source=buggy,
        candidate_source=repaired,
        expected_patch_scope="local_block",
        top_module="mux_4to1_case",
    )
    second = materialize_rtl_ast_semantic_patch(
        buggy_source=buggy,
        candidate_source=repaired,
        expected_patch_scope="local_block",
        top_module="mux_4to1_case",
    )
    assert first == second
    assert first["changed_modules"] == ["mux_4to1_case"]
    assert first["changed_blocks"] == [
        "module:mux_4to1_case/case:0"
    ]
    assert len(first["changed_ast_nodes"]) == 3
    assert first["operator_classes"] == ["constant_replacement"]
    assert first["changed_signal_roles"] == [
        "observable_output",
        "primary_input",
    ]
    normalized = first["normalized_ast_patch"]
    assert normalized["schema_version"] == AST_SEMANTIC_PATCH_SCHEMA
    assert normalized["changed_token_count"] == 3
    assert len(normalized["operations"]) == 3


@pytest.mark.parametrize(
    "candidate,match",
    [
        ("same", "no semantic token change"),
        ("module broken(", "missing endmodule"),
    ],
)
def test_runner_semantic_materializer_fails_closed(candidate, match):
    buggy, _repaired = _sources()
    replacement = buggy if candidate == "same" else candidate
    with pytest.raises(RtlAstMaterializationViolation, match=match):
        materialize_rtl_ast_semantic_patch(
            buggy_source=buggy,
            candidate_source=replacement,
            expected_patch_scope="local_block",
            top_module="mux_4to1_case",
        )


def test_parser_backed_provider_rejects_tampered_semantic_metadata():
    buggy, repaired = _sources()
    provider = ParserBackedSemanticSignatureProvider()
    proposal = {
        "candidate_id": "C0",
        "slot_index": 0,
        "lens_id": "control_v1",
        "replacement_rtl": repaired,
        "edit": "restore case labels",
    }
    artifact = {
        "buggy_rtl_source": buggy,
        "buggy_rtl_hash": hash_payload(buggy),
        "top_module": "mux_4to1_case",
    }
    semantic = provider.derive_semantic_patch(
        patch_payload=proposal,
        current_case_artifact=artifact,
        expected_patch_scope="local_block",
    )
    authoritative = {**proposal, "semantic_patch": semantic}
    receipt = verify_semantic_signature_receipt(
        provider.materialize(
            candidate_id="C0",
            lens_id="control_v1",
            patch_hash=hash_payload(proposal),
            patch_payload=authoritative,
            expected_patch_scope="local_block",
            current_case_artifact=artifact,
        ),
        expected_provider_hash=provider.provider_hash,
    )
    assert receipt["signature"]["changed_modules"] == [
        "mux_4to1_case"
    ]

    tampered = {
        **authoritative,
        "semantic_patch": {
            **semantic,
            "changed_blocks": ["model:claimed:block"],
        },
    }
    with pytest.raises(
        SemanticSignatureViolation,
        match="differs from runner-owned RTL AST diff",
    ):
        provider.materialize(
            candidate_id="C0",
            lens_id="control_v1",
            patch_hash=hash_payload(proposal),
            patch_payload=tampered,
            expected_patch_scope="local_block",
            current_case_artifact=artifact,
        )


def test_parser_backed_provider_rejects_model_supplied_semantic_patch():
    buggy, repaired = _sources()
    provider = ParserBackedSemanticSignatureProvider()
    with pytest.raises(
        SemanticSignatureViolation,
        match="no formal authority",
    ):
        provider.derive_semantic_patch(
            patch_payload={
                "replacement_rtl": repaired,
                "edit": "repair",
                "semantic_patch": {},
            },
            current_case_artifact={
                "buggy_rtl_source": buggy,
                "top_module": "mux_4to1_case",
            },
            expected_patch_scope="local_block",
        )


def test_parser_backed_provider_routes_no_change_to_rejected_signature():
    buggy, _repaired = _sources()
    provider = ParserBackedSemanticSignatureProvider()
    semantic = provider.derive_semantic_patch(
        patch_payload={
            "replacement_rtl": buggy,
            "edit": "no effective change",
        },
        current_case_artifact={
            "buggy_rtl_source": buggy,
            "top_module": "mux_4to1_case",
        },
        expected_patch_scope="local_block",
    )
    assert semantic["changed_blocks"] == []
    assert semantic["operator_classes"] == [
        "ast_materialization_rejected"
    ]
    assert semantic["normalized_ast_patch"]["admission"] == "rejected"
