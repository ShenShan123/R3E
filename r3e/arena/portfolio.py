"""Formal arena binding for runner-owned candidate portfolio evaluation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from r3e.arena.conformance import validate_toolchain_fingerprint
from r3e.blue.portfolio.audit import verify_blue_evaluation
from r3e.blue.portfolio.allocator import (
    OfflineAdaptiveAllocator,
    load_offline_allocator_state,
)
from r3e.blue.portfolio.candidate_executor import CandidatePortfolioExecutor
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.router import load_descriptor_router
from r3e.blue.portfolio.portfolio_control import (
    load_portfolio_template_registry,
)
from r3e.blue.portfolio.semantic_signature import (
    StructuredSemanticSignatureProvider,
)
from r3e.blue.portfolio.schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
)
from r3e.blue.portfolio.statistics import (
    build_challenge_portfolio_statistics,
)
from r3e.policy.schema import PolicyState
from r3e.memory.schema import ExecutionPlan
from r3e.protocol.hashing import hash_payload, read_json
from r3e.red.challenge import evaluate_challenge


LEGACY_BLUE_EVALUATION_AUTHORITY = "legacy_adapter_evaluation_v1"
CANDIDATE_PORTFOLIO_AUTHORITY = "candidate_portfolio_v1"
BLUE_EVALUATION_AUTHORITIES = {
    LEGACY_BLUE_EVALUATION_AUTHORITY,
    CANDIDATE_PORTFOLIO_AUTHORITY,
}


class ArenaPortfolioViolation(RuntimeError):
    """Raised when arena portfolio authority is missing or inconsistent."""


def extract_grounded_failure_descriptor(
    poison: Mapping[str, Any],
) -> dict[str, Any]:
    authority = poison.get("grounded_authority_bundle")
    if not isinstance(authority, Mapping):
        raise ArenaPortfolioViolation(
            "candidate portfolio challenge requires Grounded authority"
        )
    descriptor = authority.get("failure_descriptor")
    if not isinstance(descriptor, Mapping):
        raise ArenaPortfolioViolation(
            "candidate portfolio challenge lacks FailureDescriptor"
        )
    validity = poison.get("validity")
    evidence = (
        validity.get("evidence")
        if isinstance(validity, Mapping)
        else None
    )
    if not isinstance(validity, Mapping) or validity.get(
        "proven_valid"
    ) is not True:
        raise ArenaPortfolioViolation(
            "candidate portfolio challenge is not proven valid"
        )
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("failure_descriptor_hash")
        != descriptor.get("descriptor_hash")
    ):
        raise ArenaPortfolioViolation(
            "FailureDescriptor is not bound to Grounded validity"
        )
    return deepcopy(dict(descriptor))


class ArenaCandidatePortfolioAuthority:
    """Bind one frozen portfolio/provider/verifier to formal arena challenge."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        config: Mapping[str, Any],
        provider: Any,
        verifier: Any,
        semantic_signature_provider: Any | None = None,
        allocator: Any | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.config = deepcopy(dict(config))
        registry_path = self.root / str(
            self.config.get(
                "blue_lens_registry",
                "configs/blue/lens_registry_v1.json",
            )
        )
        portfolio_path = self.root / str(
            self.config.get("blue_candidate_portfolio") or ""
        )
        if not portfolio_path.is_file():
            raise ArenaPortfolioViolation(
                "blue candidate portfolio config is required"
            )
        self.registry = load_lens_registry(
            registry_path, project_root=self.root
        )
        self.portfolio = CandidatePortfolio.from_dict(
            read_json(portfolio_path)
        )
        if self.portfolio.lens_registry_hash != self.registry.registry_hash:
            raise ArenaPortfolioViolation(
                "arena portfolio/lens registry binding mismatch"
            )
        self.router = None
        router_path_value = str(
            self.config.get("blue_descriptor_router") or ""
        )
        if self.portfolio.mode == "descriptor_routed":
            router_path = self.root / router_path_value
            if not router_path_value or not router_path.is_file():
                raise ArenaPortfolioViolation(
                    "descriptor-routed arena requires frozen router config"
                )
            self.router = load_descriptor_router(router_path)
            if self.router.router_hash != self.portfolio.router_hash:
                raise ArenaPortfolioViolation(
                    "arena portfolio/router binding mismatch"
                )
        elif router_path_value:
            raise ArenaPortfolioViolation(
                "static arena portfolio cannot declare descriptor router"
            )
        self.allocator = None
        if self.portfolio.mode == "adaptive":
            state_path_value = str(
                self.config.get("blue_offline_allocator_state") or ""
            )
            state_path = self.root / state_path_value
            if not state_path_value or not state_path.is_file():
                raise ArenaPortfolioViolation(
                    "adaptive arena requires frozen allocator state"
                )
            if allocator is None:
                allocator = OfflineAdaptiveAllocator(
                    load_offline_allocator_state(state_path)
                )
            declared_allocator_hash = str(
                self.config.get("blue_offline_allocator_hash") or ""
            )
            actual_allocator_hash = str(
                getattr(allocator, "allocator_hash", "") or ""
            )
            if (
                actual_allocator_hash != declared_allocator_hash
                or declared_allocator_hash
                != self.portfolio.allocator_hash
            ):
                raise ArenaPortfolioViolation(
                    "offline allocator differs from round config"
                )
            self.allocator = allocator
        elif allocator is not None or self.config.get(
            "blue_offline_allocator_state"
        ):
            raise ArenaPortfolioViolation(
                "non-adaptive arena cannot declare offline allocator"
            )
        self.portfolio_template_registry = None
        template_registry_value = str(
            self.config.get("blue_portfolio_template_registry") or ""
        )
        if template_registry_value:
            try:
                self.portfolio_template_registry = (
                    load_portfolio_template_registry(
                        self.root / template_registry_value,
                        project_root=self.root,
                    )
                )
            except Exception as exc:
                raise ArenaPortfolioViolation(
                    "portfolio template registry is invalid"
                ) from exc
        declared_fingerprint = validate_toolchain_fingerprint(
            self.config.get(
                "blue_candidate_provider_toolchain_fingerprint"
            )
            or {}
        )
        actual_fingerprint = validate_toolchain_fingerprint(
            getattr(provider, "toolchain_fingerprint", None) or {}
        )
        if actual_fingerprint != declared_fingerprint:
            raise ArenaPortfolioViolation(
                "candidate provider toolchain differs from round config"
            )
        declared_verifier_hash = str(
            self.config.get("blue_candidate_verifier_hash") or ""
        )
        actual_verifier_hash = str(
            getattr(verifier, "verifier_hash", "") or ""
        )
        if (
            not declared_verifier_hash.startswith("sha256:")
            or actual_verifier_hash != declared_verifier_hash
        ):
            raise ArenaPortfolioViolation(
                "candidate verifier differs from round config"
            )
        self.provider = provider
        self.verifier = verifier
        if semantic_signature_provider is None:
            semantic_signature_provider = StructuredSemanticSignatureProvider(
                provider_hash=(
                    self.portfolio.semantic_signature_provider_hash
                )
            )
        declared_semantic_provider_hash = str(
            self.config.get(
                "blue_semantic_signature_provider_hash"
            )
            or self.portfolio.semantic_signature_provider_hash
        )
        actual_semantic_provider_hash = str(
            getattr(
                semantic_signature_provider, "provider_hash", ""
            )
            or ""
        )
        if (
            declared_semantic_provider_hash
            != self.portfolio.semantic_signature_provider_hash
            or actual_semantic_provider_hash
            != declared_semantic_provider_hash
        ):
            raise ArenaPortfolioViolation(
                "semantic signature provider differs from round config"
            )
        self.semantic_signature_provider = semantic_signature_provider
        self.semantic_signature_provider_hash = (
            declared_semantic_provider_hash
        )
        self.provider_fingerprint = declared_fingerprint
        self.verifier_hash = declared_verifier_hash
        self.executor = CandidatePortfolioExecutor(
            registry=self.registry,
            portfolio=self.portfolio,
            provider=provider,
            verifier=verifier,
            semantic_signature_provider=semantic_signature_provider,
            allocator=self.allocator,
            project_root=self.root,
            router=self.router,
            portfolio_template_registry=(
                self.portfolio_template_registry
            ),
        )

    @property
    def authority_hash(self) -> str:
        authority = {
            "authority": CANDIDATE_PORTFOLIO_AUTHORITY,
            "lens_registry_hash": self.registry.registry_hash,
            "portfolio_hash": self.portfolio.portfolio_hash,
            "effective_portfolio_hash": (
                self.portfolio.effective_portfolio_hash
            ),
            "descriptor_router_hash": (
                self.router.router_hash if self.router is not None else ""
            ),
            "provider_toolchain_fingerprint": self.provider_fingerprint,
            "verifier_hash": self.verifier_hash,
            "semantic_signature_provider_hash": (
                self.semantic_signature_provider_hash
            ),
            "offline_allocator_hash": (
                self.allocator.allocator_hash
                if self.allocator is not None else ""
            ),
        }
        if self.portfolio_template_registry is not None:
            authority["portfolio_template_registry_hash"] = (
                self.portfolio_template_registry.registry_hash
            )
        return hash_payload(authority)

    def assert_policy_authorized(self, policy: PolicyState) -> None:
        if policy.schema_version != "r3e-policy-v3":
            raise ArenaPortfolioViolation(
                "formal arena candidate portfolio requires Policy V3"
            )
        if (
            policy.candidate_portfolio_binding
            != build_candidate_portfolio_binding(self.portfolio)
        ):
            raise ArenaPortfolioViolation(
                "candidate portfolio is not authorized by active Policy V3"
            )

    def evaluate(
        self,
        policy: PolicyState,
        poison: Mapping[str, Any],
        seed: int,
        execution_plan: ExecutionPlan | None = None,
    ) -> dict[str, Any]:
        self.assert_policy_authorized(policy)
        descriptor = extract_grounded_failure_descriptor(poison)
        result = self.executor.execute(
            policy=policy,
            case={"case_id": str(
                poison.get("case_id")
                or poison.get("poison_id")
                or ""
            )},
            descriptor=descriptor,
            run_seed=seed,
            execution_plan=execution_plan,
        )
        return verify_blue_evaluation(
            result,
            policy=policy,
            registry=self.registry,
            portfolio=self.portfolio,
            provider=self.provider,
            expected_verifier_hash=self.verifier_hash,
            expected_semantic_signature_provider_hash=(
                self.semantic_signature_provider_hash
            ),
            router=self.router,
            allocator=self.allocator,
            descriptor=descriptor,
            execution_plan=execution_plan,
            portfolio_template_registry=(
                self.portfolio_template_registry
            ),
        )

    def evaluate_challenge(
        self,
        policy: PolicyState,
        poison: dict[str, Any],
        *,
        seeds: list[int],
        execution_plan: ExecutionPlan | None = None,
    ) -> dict[str, Any]:
        result = evaluate_challenge(
            policy,
            poison,
            seeds=seeds,
            evaluator=lambda current_policy, current_poison, seed: (
                self.evaluate(
                    current_policy,
                    current_poison,
                    seed,
                    execution_plan=execution_plan,
                )
            ),
        )
        result["portfolio_statistics"] = (
            build_challenge_portfolio_statistics(
                result["blue_results"]
            )
        )
        result["challenge_result_hash"] = hash_payload({
            key: value for key, value in result.items()
            if key != "challenge_result_hash"
        })
        return result
