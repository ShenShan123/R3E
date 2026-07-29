"""Deterministic ACP-0/ACP-1 executable protocol fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from r3e.memory.schema import FailureDescriptor
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json

from .audit import verify_blue_evaluation
from .allocator import (
    OfflineAdaptiveAllocator,
    load_offline_allocator_state,
)
from .candidate_executor import CandidatePortfolioExecutor
from .fake import (
    DeterministicFakeCandidateProvider,
    DeterministicFakeCandidateVerifier,
)
from .lens_registry import load_lens_registry
from .router import load_descriptor_router
from .schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
    build_implicit_homogeneous_portfolio,
)


def run_fake_candidate_protocol(
    *,
    project_root: str | Path,
    out: str | Path,
    run_seed: int = 101,
    portfolio_path: str | Path | None = None,
    router_path: str | Path | None = None,
    allocator_state_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    base = PolicyState.from_dict(
        read_json(root / "configs/base_policy/frozen_base_policy_v1.json")
    )
    registry = load_lens_registry(
        root / "configs/blue/lens_registry_v1.json",
        project_root=root,
    )
    explicit_portfolio = None
    if portfolio_path is not None:
        source = Path(portfolio_path)
        if not source.is_absolute():
            source = root / source
        explicit_portfolio = CandidatePortfolio.from_dict(read_json(source))
    router = None
    allocator = None
    if explicit_portfolio is not None and (
        explicit_portfolio.mode == "descriptor_routed"
    ):
        if router_path is None:
            raise ValueError(
                "descriptor-routed fake fixture requires router_path"
            )
        router_source = Path(router_path)
        if not router_source.is_absolute():
            router_source = root / router_source
        router = load_descriptor_router(router_source)
    if explicit_portfolio is not None and (
        explicit_portfolio.mode == "adaptive"
    ):
        if allocator_state_path is None:
            raise ValueError(
                "adaptive fake fixture requires allocator_state_path"
            )
        allocator_source = Path(allocator_state_path)
        if not allocator_source.is_absolute():
            allocator_source = root / allocator_source
        allocator = OfflineAdaptiveAllocator(
            load_offline_allocator_state(allocator_source)
        )
    policy = base.with_updates(
        policy_id=(
            "B_ACP2_FAKE"
            if explicit_portfolio is not None
            and explicit_portfolio.mode == "descriptor_routed"
            else (
                "B_ACP1_FAKE"
                if explicit_portfolio is not None
                else "B_ACP0_FAKE"
            )
        ),
        schema_version=(
            "r3e-policy-v3"
            if explicit_portfolio is not None
            else base.schema_version
        ),
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=1,
        status="candidate",
        configuration={
            **base.configuration,
            "n_candidates": 3,
            "candidate_selection": "verifier_guided",
        },
        candidate_portfolio_binding=(
            build_candidate_portfolio_binding(explicit_portfolio)
            if explicit_portfolio is not None
            else {}
        ),
    )
    portfolio = (
        explicit_portfolio
        if explicit_portfolio is not None
        else build_implicit_homogeneous_portfolio(policy, registry)
    )
    descriptor = FailureDescriptor.create({
        "oracle_stage": "grounded_simulation_and_formal",
        "sequential_context": True,
        "temporal_relation": "candidate_lags_golden",
        "cycle_offset_bucket": 1,
        "affected_roles": ["observable_output"],
        "assignment_type": "nonblocking",
        "cone_depth_bucket": "depth_2_3",
        "mismatch_pattern": "late_transition",
        "first_divergence_bucket": "cycle_1",
        "first_divergence_signal": "out",
        "observable_artifact_hashes": {
            "oracle": hash_payload({"fixture": "oracle"}),
            "formal": hash_payload({"fixture": "formal"}),
        },
    }).to_dict()
    provider = DeterministicFakeCandidateProvider()
    verifier = DeterministicFakeCandidateVerifier(successful_slots={1})
    result = CandidatePortfolioExecutor(
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        verifier=verifier,
        project_root=root,
        router=router,
        allocator=allocator,
    ).execute(
        policy=policy,
        case={"case_id": "ACP0_DETERMINISTIC_FIXTURE"},
        descriptor=descriptor,
        run_seed=run_seed,
    )
    verify_blue_evaluation(
        result,
        policy=policy,
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        expected_verifier_hash=verifier.verifier_hash,
        router=router,
        allocator=allocator,
        descriptor=descriptor,
    )
    atomic_write_json(out, result)
    return {
        "schema_version": result["schema_version"],
        "policy_hash": policy.policy_hash,
        "portfolio_hash": portfolio.effective_portfolio_hash,
        "portfolio_mode": portfolio.mode,
        "authorized_lens_ids": list(portfolio.lens_ids),
        "lens_ids": [
            row["lens_id"]
            for row in result["candidate_generation_receipts"]
        ],
        "candidate_count": len(result["candidate_generation_receipts"]),
        "semantic_signature_provider_hash": result[
            "semantic_signature_provider_hash"
        ],
        "offline_allocator_hash": (
            result["allocator_receipt"].get("allocator_state_hash")
            if result["allocator_receipt"] else ""
        ),
        "used_global_allocator_fallback": (
            result["allocator_receipt"].get("used_global_fallback")
            if result["allocator_receipt"] else False
        ),
        "semantic_diversity_milli": result[
            "portfolio_diversity_receipt"
        ]["semantic_diversity_milli"],
        "lens_collapse": result["portfolio_diversity_receipt"][
            "lens_collapse"
        ],
        "selected_candidate_id": result["selection_receipt"][
            "selected_candidate_id"
        ],
        "oracle_ok": result["oracle_ok"],
        "portfolio_execution_hash": result["portfolio_execution_hash"],
        "out": str(Path(out)),
    }


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-seed", type=int, default=101)
    parser.add_argument(
        "--portfolio",
        help="optional repository-relative explicit CandidatePortfolio",
    )
    parser.add_argument(
        "--allocator-state",
        help="allocator state required by adaptive CandidatePortfolio",
    )
    parser.add_argument(
        "--router",
        help="router required by descriptor-routed CandidatePortfolio",
    )
    args = parser.parse_args()
    summary = run_fake_candidate_protocol(
        project_root=args.project_root,
        out=args.out,
        run_seed=args.run_seed,
        portfolio_path=args.portfolio,
        router_path=args.router,
        allocator_state_path=args.allocator_state,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    _main()
