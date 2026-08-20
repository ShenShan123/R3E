"""Offline reconstruction of ACP-0 BlueEvaluation V2 authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.arena.conformance import AdapterConformanceGate
from r3e.memory.execution_trace import PortfolioExecutionTraceRecorder
from r3e.protocol.hashing import hash_payload

from .candidate_executor import BLUE_EVALUATION_SCHEMA
from .candidate_receipts import (
    verify_generation_receipt,
    verify_selection_receipt,
    verify_verification_receipt,
)
from .schema import AllocationPlan, CandidatePortfolio, LensRegistry
from .router import DescriptorRouter
from .portfolio_control import PortfolioTemplateRegistry
from r3e.memory.schema import ExecutionPlan
from .semantic_signature import verify_semantic_signature_receipt
from .statistics import build_portfolio_diversity_receipt


class PortfolioAuditViolation(RuntimeError):
    """Raised when a BlueEvaluation V2 authority chain cannot be rebuilt."""


def verify_blue_evaluation(
    raw: Mapping[str, Any],
    *,
    policy: Any,
    registry: LensRegistry,
    portfolio: CandidatePortfolio,
    provider: Any | None = None,
    provider_fingerprints: list[Mapping[str, Any]] | None = None,
    expected_verifier_hash: str | None = None,
    expected_semantic_signature_provider_hash: str | None = None,
    router: DescriptorRouter | None = None,
    allocator: Any | None = None,
    descriptor: Mapping[str, Any] | None = None,
    execution_plan: ExecutionPlan | None = None,
    portfolio_template_registry: PortfolioTemplateRegistry | None = None,
) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    expected_fields = {
        "schema_version",
        "policy_instance_hash",
        "effective_policy_hash",
        "policy_hash",
        "case_id",
        "run_seed",
        "seed",
        "descriptor_hash",
        "portfolio_hash",
        "candidate_verifier_hash",
        "semantic_signature_provider_hash",
        "router_receipt",
        "allocator_receipt",
        "portfolio_control_receipt",
        "memory_execution_plan_hash",
        "allocation_plan",
        "allocation_plan_hash",
        "candidate_provider_receipts",
        "candidate_generation_receipts",
        "candidate_semantic_signature_receipts",
        "candidate_verification_receipts",
        "portfolio_diversity_receipt",
        "selection_receipt",
        "execution_trace",
        "selected_patch_hash",
        "successful_patch_hash",
        "oracle_ok",
        "resource_usage",
        "portfolio_execution_hash",
    }
    if set(payload) != expected_fields:
        raise PortfolioAuditViolation("BlueEvaluation V2 fields mismatch")
    if payload["schema_version"] != BLUE_EVALUATION_SCHEMA:
        raise PortfolioAuditViolation("BlueEvaluation V2 schema mismatch")
    if (
        payload["policy_instance_hash"] != policy.policy_instance_hash
        or payload["policy_hash"] != policy.policy_hash
        or payload["effective_policy_hash"] != policy.effective_policy_hash
    ):
        raise PortfolioAuditViolation("BlueEvaluation policy binding mismatch")
    if payload["run_seed"] != payload["seed"]:
        raise PortfolioAuditViolation("run seed alias mismatch")
    if registry.registry_hash != portfolio.lens_registry_hash:
        raise PortfolioAuditViolation("portfolio registry binding mismatch")
    if payload["portfolio_hash"] != portfolio.effective_portfolio_hash:
        raise PortfolioAuditViolation("BlueEvaluation portfolio binding mismatch")
    if (
        expected_verifier_hash is not None
        and payload["candidate_verifier_hash"] != expected_verifier_hash
    ):
        raise PortfolioAuditViolation("candidate verifier binding mismatch")
    semantic_provider_hash = (
        expected_semantic_signature_provider_hash
        or portfolio.semantic_signature_provider_hash
    )
    if (
        payload["semantic_signature_provider_hash"]
        != semantic_provider_hash
        or semantic_provider_hash
        != portfolio.semantic_signature_provider_hash
    ):
        raise PortfolioAuditViolation(
            "semantic signature provider binding mismatch"
        )
    portfolio_control_receipt: dict[str, Any] = {}
    if payload["portfolio_control_receipt"]:
        if (
            execution_plan is None
            or portfolio_template_registry is None
            or payload["memory_execution_plan_hash"]
            != execution_plan.plan_hash
        ):
            raise PortfolioAuditViolation(
                "portfolio control plan authority is required"
            )
        try:
            portfolio_control_receipt = (
                portfolio_template_registry.build_receipt(
                    execution_plan.controls,
                    policy=policy,
                    execution_plan_hash=execution_plan.plan_hash,
                )
            )
        except Exception as exc:
            raise PortfolioAuditViolation(
                "portfolio control receipt is invalid"
            ) from exc
        if payload["portfolio_control_receipt"] != portfolio_control_receipt:
            raise PortfolioAuditViolation(
                "portfolio control receipt is not reconstructable"
            )
    elif payload["memory_execution_plan_hash"] != "":
        if (
            execution_plan is None
            or payload["memory_execution_plan_hash"]
            != execution_plan.plan_hash
            or execution_plan.effective_policy_hash
            != policy.effective_policy_hash
            or execution_plan.activated_memory_ids
            or execution_plan.controls.get("portfolio_allocation_mode")
            != "policy_default"
            or "candidate_portfolio_template_id"
            in execution_plan.controls
        ):
            raise PortfolioAuditViolation(
                "default portfolio memory plan is not reconstructable"
            )
    router_receipt: dict[str, Any] = {}
    if portfolio.mode == "descriptor_routed" and not portfolio_control_receipt:
        if router is None or descriptor is None:
            raise PortfolioAuditViolation(
                "descriptor router authority is required"
            )
        try:
            router_receipt = router.verify_receipt(
                payload["router_receipt"],
                descriptor,
                portfolio=portfolio,
                registry=registry,
            )
        except Exception as exc:
            raise PortfolioAuditViolation(
                "descriptor router receipt is invalid"
            ) from exc
    elif payload["router_receipt"] != {}:
        raise PortfolioAuditViolation(
            "static portfolio carries descriptor router receipt"
        )
    allocator_receipt: dict[str, Any] = {}
    if portfolio.mode == "adaptive" and not portfolio_control_receipt:
        if allocator is None or descriptor is None:
            raise PortfolioAuditViolation(
                "offline allocator authority is required"
            )
        try:
            allocator_receipt = allocator.verify_receipt(
                payload["allocator_receipt"],
                descriptor,
                portfolio=portfolio,
                registry=registry,
            )
        except Exception as exc:
            raise PortfolioAuditViolation(
                "offline allocator receipt is invalid"
            ) from exc
    elif payload["allocator_receipt"] != {}:
        raise PortfolioAuditViolation(
            "non-adaptive portfolio carries allocator receipt"
        )
    try:
        plan = AllocationPlan.from_dict(payload["allocation_plan"])
    except Exception as exc:
        raise PortfolioAuditViolation("allocation plan is invalid") from exc
    if (
        payload["allocation_plan_hash"] != plan.plan_hash
        or plan.effective_policy_hash != policy.effective_policy_hash
        or plan.descriptor_hash != payload["descriptor_hash"]
        or plan.portfolio_hash != portfolio.effective_portfolio_hash
        or plan.run_seed != payload["run_seed"]
    ):
        raise PortfolioAuditViolation("allocation plan authority mismatch")
    try:
        expected_plan = AllocationPlan.create(
            effective_policy_hash=policy.effective_policy_hash,
            descriptor_hash=payload["descriptor_hash"],
            portfolio=portfolio,
            registry=registry,
            run_seed=payload["run_seed"],
            router_receipt=router_receipt,
            allocator_receipt=allocator_receipt,
            portfolio_control_receipt=portfolio_control_receipt,
        )
    except Exception as exc:
        raise PortfolioAuditViolation(
            "allocation plan cannot be reconstructed"
        ) from exc
    if payload["allocation_plan"] != expected_plan.to_dict():
        raise PortfolioAuditViolation(
            "allocation plan differs from descriptor routing"
        )

    provider_receipts = payload["candidate_provider_receipts"]
    try:
        generations = [
            verify_generation_receipt(row)
            for row in payload["candidate_generation_receipts"]
        ]
        signatures = [
            verify_semantic_signature_receipt(
                row,
                expected_provider_hash=semantic_provider_hash,
            )
            for row in payload[
                "candidate_semantic_signature_receipts"
            ]
        ]
        verifications = [
            verify_verification_receipt(row)
            for row in payload["candidate_verification_receipts"]
        ]
    except Exception as exc:
        raise PortfolioAuditViolation("candidate receipt is invalid") from exc
    if not isinstance(provider_receipts, list) or (
        len(provider_receipts) != len(generations)
        or len(generations) != len(signatures)
        or len(generations) != len(verifications)
        or len(generations) != plan.candidate_budget
    ):
        raise PortfolioAuditViolation("candidate receipt cardinality mismatch")
    if provider is None:
        if not provider_fingerprints:
            raise PortfolioAuditViolation(
                "provider fingerprint authority is required"
            )
        provider = type(
            "_ProviderFingerprintAuthority",
            (),
            {"conformance_fingerprints": list(provider_fingerprints)},
        )()
    try:
        gate = AdapterConformanceGate(provider)
        verified_provider_receipts = [
            gate.validate("generate_blue_candidate", row)
            for row in provider_receipts
        ]
    except Exception as exc:
        raise PortfolioAuditViolation("provider receipt is invalid") from exc
    if bool(
        getattr(provider, "requires_current_case_artifact", False)
    ) and any(
        "semantic_patch" in row.get("patch_payload", {})
        for row in verified_provider_receipts
    ):
        raise PortfolioAuditViolation(
            "formal provider receipt carries semantic patch authority"
        )
    for index, (
        provider_row,
        generation,
        signature,
        verification,
        slot,
    ) in enumerate(
        zip(
            verified_provider_receipts,
            generations,
            signatures,
            verifications,
            plan.slots,
            strict=True,
        )
    ):
        if (
            provider_row["result_hash"] != generation["provider_receipt_hash"]
            or provider_row["patch_payload_hash"]
            != generation["patch_payload_hash"]
            or generation["patch_payload_hash"] != verification["patch_hash"]
            or generation["patch_payload_hash"] != signature["patch_hash"]
            or generation["candidate_id"] != verification["candidate_id"]
            or generation["candidate_id"] != signature["candidate_id"]
            or signature["lens_id"] != slot["lens_id"]
            or signature["signature"]["signature_hash"]
            != verification["semantic_patch_signature_hash"]
            or generation["slot_index"] != index
            or generation["lens_id"] != slot["lens_id"]
            or generation["lens_hash"] != slot["lens_hash"]
        ):
            raise PortfolioAuditViolation("candidate receipt chain mismatch")
    try:
        diversity = build_portfolio_diversity_receipt(
            signatures,
            verifications,
            provider_calls=len(provider_receipts),
            verifier_calls=len(verifications),
            total_tokens=sum(
                row["input_tokens"] + row["output_tokens"]
                for row in generations
            ),
        )
    except Exception as exc:
        raise PortfolioAuditViolation(
            "portfolio diversity receipt is invalid"
        ) from exc
    if payload["portfolio_diversity_receipt"] != diversity:
        raise PortfolioAuditViolation(
            "portfolio diversity receipt is not reconstructable"
        )
    try:
        selection = verify_selection_receipt(
            payload["selection_receipt"], verifications
        )
    except Exception as exc:
        raise PortfolioAuditViolation(
            "selection receipt is not reconstructable"
        ) from exc
    selected = next(
        (
            row for row in verifications
            if row["candidate_id"] == selection["selected_candidate_id"]
        ),
        None,
    )
    expected_patch = (
        selected["patch_hash"]
        if selected is not None
        else hash_payload({"selected_candidate_id": None})
    )
    if (
        payload["selected_patch_hash"] != expected_patch
        or payload["successful_patch_hash"]
        != (expected_patch if selected is not None else "")
        or payload["oracle_ok"] is not (selected is not None)
    ):
        raise PortfolioAuditViolation("selected patch authority mismatch")
    usage = payload["resource_usage"]
    expected_usage = {
        "provider_calls": len(provider_receipts),
        "llm_calls": len(provider_receipts),
        "verifier_calls": len(verifications),
        "input_tokens": sum(row["input_tokens"] for row in generations),
        "output_tokens": sum(row["output_tokens"] for row in generations),
        "wall_time_seconds": 0.0,
    }
    if usage != expected_usage:
        raise PortfolioAuditViolation("BlueEvaluation resource usage mismatch")
    recorder = PortfolioExecutionTraceRecorder(plan.to_dict())
    for generation, signature, verification in zip(
        generations, signatures, verifications, strict=True
    ):
        recorder.record_generation(generation)
        recorder.record_semantic_signature(signature)
        recorder.record_verification(verification)
    recorder.record_diversity(diversity)
    recorder.record_selection(selection)
    expected_trace = recorder.finalize(
        generation_receipts=generations,
        semantic_signature_receipts=signatures,
        verification_receipts=verifications,
        diversity_receipt=diversity,
        selection_receipt=selection,
        provider_calls=len(provider_receipts),
        total_tokens=(
            expected_usage["input_tokens"] + expected_usage["output_tokens"]
        ),
        max_provider_calls=int(policy.budgets["max_llm_calls_per_case"]),
        max_tokens=int(policy.budgets["max_tokens_per_case"]),
    )
    if payload["execution_trace"] != expected_trace:
        raise PortfolioAuditViolation(
            "portfolio execution trace is not reconstructable"
        )
    expected_execution_hash = hash_payload({
        key: value
        for key, value in payload.items()
        if key != "portfolio_execution_hash"
    })
    if payload["portfolio_execution_hash"] != expected_execution_hash:
        raise PortfolioAuditViolation("BlueEvaluation execution hash mismatch")
    return payload
