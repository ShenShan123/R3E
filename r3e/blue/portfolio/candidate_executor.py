"""Runner-owned candidate portfolio execution and BlueEvaluation V2."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from r3e.arena.conformance import (
    AdapterConformanceGate,
    AdapterConformanceViolation,
)
from r3e.memory.execution_trace import PortfolioExecutionTraceRecorder
from r3e.memory.schema import ExecutionPlan, FailureDescriptor
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload

from .candidate_receipts import (
    build_generation_receipt,
    build_selection_receipt,
    build_verification_receipt,
)
from .schema import (
    AllocationPlan,
    CandidatePortfolio,
    LensRegistry,
    build_candidate_portfolio_binding,
    build_implicit_homogeneous_portfolio,
)
from .router import DescriptorRouter
from .portfolio_control import (
    PortfolioControlViolation,
    PortfolioTemplateRegistry,
)
from .semantic_signature import (
    StructuredSemanticSignatureProvider,
    verify_semantic_signature_receipt,
)
from .statistics import build_portfolio_diversity_receipt


BLUE_EVALUATION_SCHEMA = "r3e-blue-evaluation-v2"


class CandidatePortfolioExecutionViolation(RuntimeError):
    """Raised when provider execution exceeds or bypasses ACP authority."""


class CandidatePortfolioExecutor:
    """Execute one complete candidate portfolio under runner-owned authority."""

    def __init__(
        self,
        *,
        registry: LensRegistry,
        portfolio: CandidatePortfolio,
        provider: Any,
        verifier: Callable[..., Mapping[str, Any]],
        semantic_signature_provider: Any | None = None,
        project_root: str | Path,
        router: DescriptorRouter | None = None,
        allocator: Any | None = None,
        portfolio_template_registry: PortfolioTemplateRegistry | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if portfolio.lens_registry_hash != registry.registry_hash:
            raise CandidatePortfolioExecutionViolation(
                "portfolio/lens registry binding mismatch"
            )
        if not callable(getattr(provider, "generate_candidate", None)):
            raise CandidatePortfolioExecutionViolation(
                "candidate provider lacks generate_candidate"
            )
        if not callable(verifier):
            raise CandidatePortfolioExecutionViolation(
                "candidate verifier must be callable"
            )
        self.registry = registry
        self.portfolio = portfolio
        self.router = router
        if portfolio.mode == "descriptor_routed":
            if router is None or router.router_hash != portfolio.router_hash:
                raise CandidatePortfolioExecutionViolation(
                    "descriptor portfolio requires its frozen router"
                )
        elif router is not None:
            raise CandidatePortfolioExecutionViolation(
                "static portfolio cannot consume descriptor router"
            )
        self.allocator = allocator
        self.portfolio_template_registry = portfolio_template_registry
        if portfolio.mode == "adaptive":
            if (
                allocator is None
                or not callable(getattr(allocator, "allocate", None))
                or str(getattr(allocator, "allocator_hash", "") or "")
                != portfolio.allocator_hash
            ):
                raise CandidatePortfolioExecutionViolation(
                    "adaptive portfolio requires its frozen allocator"
                )
        elif allocator is not None:
            raise CandidatePortfolioExecutionViolation(
                "non-adaptive portfolio cannot consume allocator"
            )
        self.provider = provider
        self.provider_gate = AdapterConformanceGate(provider)
        self.verifier = verifier
        self.verifier_hash = str(
            getattr(verifier, "verifier_hash", "") or ""
        )
        if not self.verifier_hash.startswith("sha256:"):
            raise CandidatePortfolioExecutionViolation(
                "candidate verifier must declare verifier_hash"
            )
        if semantic_signature_provider is None:
            semantic_signature_provider = StructuredSemanticSignatureProvider(
                provider_hash=portfolio.semantic_signature_provider_hash
            )
        if not callable(
            getattr(semantic_signature_provider, "materialize", None)
        ):
            raise CandidatePortfolioExecutionViolation(
                "semantic signature provider lacks materialize"
            )
        self.semantic_signature_provider = semantic_signature_provider
        self.semantic_signature_provider_hash = str(
            getattr(semantic_signature_provider, "provider_hash", "") or ""
        )
        if (
            self.semantic_signature_provider_hash
            != portfolio.semantic_signature_provider_hash
        ):
            raise CandidatePortfolioExecutionViolation(
                "semantic signature provider/portfolio binding mismatch"
            )
        self.project_root = Path(project_root).resolve()
        self.clock = clock

    def _prompt_asset(self, lens_id: str) -> str:
        lens = self.registry.lenses[lens_id]
        asset = (self.project_root / lens.prompt_asset_path).resolve()
        try:
            asset.relative_to(self.project_root)
        except ValueError as exc:
            raise CandidatePortfolioExecutionViolation(
                "prompt asset escapes project root"
            ) from exc
        if not asset.is_file() or hash_file(asset) != lens.prompt_asset_hash:
            raise CandidatePortfolioExecutionViolation(
                f"prompt asset authority mismatch: {lens_id}"
            )
        return asset.read_text(encoding="utf-8")

    @staticmethod
    def _current_case_evidence(
        descriptor: FailureDescriptor,
        allowed_fields: tuple[str, ...],
    ) -> dict[str, Any]:
        return {
            key: deepcopy(descriptor.features[key])
            for key in allowed_fields
            if key in descriptor.features
        }

    @staticmethod
    def _candidate_id(case_id: str, run_seed: int, slot_index: int) -> str:
        suffix = hash_payload({
            "case_id": case_id,
            "run_seed": run_seed,
            "slot_index": slot_index,
        }).split(":", 1)[1][:16]
        return f"C_{suffix}_{slot_index}"

    def _wall_gate(self, started: float, maximum: int) -> None:
        elapsed = self.clock() - started
        if elapsed < 0 or elapsed > maximum:
            raise CandidatePortfolioExecutionViolation(
                "candidate portfolio wall-time budget exceeded"
            )

    def execute(
        self,
        *,
        policy: PolicyState,
        case: Mapping[str, Any],
        descriptor: Mapping[str, Any],
        run_seed: int,
        execution_plan: ExecutionPlan | None = None,
    ) -> dict[str, Any]:
        case_id = str(case.get("case_id") or "")
        if not case_id:
            raise CandidatePortfolioExecutionViolation("case_id is required")
        try:
            failure = FailureDescriptor.from_dict(descriptor)
        except Exception as exc:
            raise CandidatePortfolioExecutionViolation(
                "ACP requires a valid current Grounded FailureDescriptor"
            ) from exc
        max_calls = int(policy.budgets["max_llm_calls_per_case"])
        max_tokens = int(policy.budgets["max_tokens_per_case"])
        max_wall = int(policy.budgets["max_wall_seconds_per_case"])
        if self.portfolio.candidate_budget > max_calls:
            raise CandidatePortfolioExecutionViolation(
                "portfolio candidate budget exceeds active policy"
            )
        if policy.schema_version == "r3e-policy-v2":
            try:
                authorized_portfolio = build_implicit_homogeneous_portfolio(
                    policy, self.registry
                )
            except Exception as exc:
                raise CandidatePortfolioExecutionViolation(
                    "Policy V2 implicit portfolio is invalid"
                ) from exc
            if (
                self.portfolio.effective_portfolio_hash
                != authorized_portfolio.effective_portfolio_hash
            ):
                raise CandidatePortfolioExecutionViolation(
                    "candidate portfolio is not authorized by active Policy V2"
                )
        elif policy.schema_version == "r3e-policy-v3":
            if (
                policy.candidate_portfolio_binding
                != build_candidate_portfolio_binding(self.portfolio)
            ):
                raise CandidatePortfolioExecutionViolation(
                    "candidate portfolio is not authorized by active Policy V3"
                )
        else:
            raise CandidatePortfolioExecutionViolation(
                "candidate portfolio requires Policy V2 or V3"
            )
        router_receipt: dict[str, Any] = {}
        allocator_receipt: dict[str, Any] = {}
        portfolio_control_receipt: dict[str, Any] = {}
        memory_execution_plan_hash = ""
        if execution_plan is not None:
            if (
                execution_plan.effective_policy_hash
                != policy.effective_policy_hash
            ):
                raise CandidatePortfolioExecutionViolation(
                    "portfolio memory plan authority mismatch"
                )
            memory_execution_plan_hash = execution_plan.plan_hash
            if execution_plan.activated_memory_ids:
                if self.portfolio_template_registry is None:
                    raise CandidatePortfolioExecutionViolation(
                        "portfolio memory plan lacks frozen template registry"
                    )
                try:
                    portfolio_control_receipt = (
                        self.portfolio_template_registry.build_receipt(
                            execution_plan.controls,
                            policy=policy,
                            execution_plan_hash=execution_plan.plan_hash,
                        )
                    )
                except PortfolioControlViolation as exc:
                    raise CandidatePortfolioExecutionViolation(
                        "portfolio memory plan exceeds Policy authority"
                    ) from exc
            elif (
                execution_plan.controls.get("portfolio_allocation_mode")
                != "policy_default"
                or "candidate_portfolio_template_id"
                in execution_plan.controls
            ):
                raise CandidatePortfolioExecutionViolation(
                    "abstained memory plan is not policy default"
                )
        elif (
            self.portfolio_template_registry is not None
            and policy.memory_binding
        ):
            raise CandidatePortfolioExecutionViolation(
                "memory-bound policy requires an explicit execution plan"
            )
        if self.router is not None and not portfolio_control_receipt:
            try:
                router_receipt = self.router.route(
                    failure,
                    portfolio=self.portfolio,
                    registry=self.registry,
                )
            except Exception as exc:
                raise CandidatePortfolioExecutionViolation(
                    "descriptor router failed authority validation"
                ) from exc
        if self.allocator is not None and not portfolio_control_receipt:
            try:
                allocator_receipt = self.allocator.allocate(
                    failure,
                    portfolio=self.portfolio,
                    registry=self.registry,
                )
            except Exception as exc:
                raise CandidatePortfolioExecutionViolation(
                    "offline allocator failed authority validation"
                ) from exc
        plan = AllocationPlan.create(
            effective_policy_hash=policy.effective_policy_hash,
            descriptor_hash=failure.descriptor_hash,
            portfolio=self.portfolio,
            registry=self.registry,
            run_seed=run_seed,
            router_receipt=router_receipt,
            allocator_receipt=allocator_receipt,
            portfolio_control_receipt=portfolio_control_receipt,
        )
        trace = PortfolioExecutionTraceRecorder(plan.to_dict())
        started = self.clock()
        provider_calls = 0
        total_tokens = 0
        provider_receipts: list[dict[str, Any]] = []
        generation_receipts: list[dict[str, Any]] = []
        semantic_signature_receipts: list[dict[str, Any]] = []
        verification_receipts: list[dict[str, Any]] = []
        expected_budget_hash = hash_payload(policy.budgets)

        for slot in plan.slots:
            lens = self.registry.lenses[slot["lens_id"]]
            evidence = self._current_case_evidence(
                failure, lens.allowed_evidence_fields
            )
            evidence_hash = hash_payload(evidence)
            prompt_asset = self._prompt_asset(lens.lens_id)
            prompt_hash = hash_payload({
                "prompt_asset_hash": lens.prompt_asset_hash,
                "current_case_evidence": evidence,
                "effective_policy_hash": policy.effective_policy_hash,
                "case_id": case_id,
            })
            candidate_id = self._candidate_id(
                case_id, plan.run_seed, slot["slot_index"]
            )
            provider_calls += 1
            if provider_calls > max_calls:
                raise CandidatePortfolioExecutionViolation(
                    "unplanned provider call exceeds active policy"
                )
            try:
                provider_output = self.provider_gate.validate(
                    "generate_blue_candidate",
                    self.provider.generate_candidate(
                        policy=policy,
                        current_case_evidence=deepcopy(evidence),
                        slot=deepcopy(slot),
                        prompt_asset=prompt_asset,
                        prompt_hash=prompt_hash,
                        candidate_id=candidate_id,
                    ),
                )
            except AdapterConformanceViolation as exc:
                raise CandidatePortfolioExecutionViolation(
                    "candidate provider failed conformance"
                ) from exc
            self._wall_gate(started, max_wall)
            if provider_output["budget_hash"] != expected_budget_hash:
                raise CandidatePortfolioExecutionViolation(
                    "candidate provider budget binding mismatch"
                )
            provider_receipts.append(deepcopy(provider_output))
            generation = build_generation_receipt(
                provider_output=provider_output,
                expected_candidate_id=candidate_id,
                expected_slot=slot,
                expected_prompt_hash=prompt_hash,
                current_case_evidence_hash=evidence_hash,
            )
            total_tokens += (
                generation["input_tokens"] + generation["output_tokens"]
            )
            if total_tokens > max_tokens:
                raise CandidatePortfolioExecutionViolation(
                    "candidate provider token budget exceeded"
                )
            generation_receipts.append(generation)
            trace.record_generation(generation)

            try:
                signature_receipt = verify_semantic_signature_receipt(
                    self.semantic_signature_provider.materialize(
                        candidate_id=candidate_id,
                        lens_id=slot["lens_id"],
                        patch_hash=generation["patch_payload_hash"],
                        patch_payload=deepcopy(
                            provider_output["patch_payload"]
                        ),
                        expected_patch_scope=policy.configuration[
                            "patch_scope"
                        ],
                    ),
                    expected_provider_hash=(
                        self.semantic_signature_provider_hash
                    ),
                )
            except Exception as exc:
                raise CandidatePortfolioExecutionViolation(
                    "semantic signature provider rejected candidate patch"
                ) from exc
            semantic_signature_receipts.append(signature_receipt)
            trace.record_semantic_signature(signature_receipt)

            verifier_output = self.verifier(
                policy=policy,
                case={"case_id": case_id},
                current_case_evidence=deepcopy(evidence),
                slot=deepcopy(slot),
                candidate_id=candidate_id,
                patch_payload=deepcopy(provider_output["patch_payload"]),
            )
            self._wall_gate(started, max_wall)
            if not isinstance(verifier_output, Mapping):
                raise CandidatePortfolioExecutionViolation(
                    "candidate verifier output must be an object"
                )
            try:
                verification = build_verification_receipt(
                    candidate_id=candidate_id,
                    patch_hash=generation["patch_payload_hash"],
                    semantic_patch_signature_hash=signature_receipt[
                        "signature"
                    ]["signature_hash"],
                    verifier_output=verifier_output,
                )
            except Exception as exc:
                raise CandidatePortfolioExecutionViolation(
                    "candidate verifier receipt failed authority validation"
                ) from exc
            verification_receipts.append(verification)
            trace.record_verification(verification)

        diversity_receipt = build_portfolio_diversity_receipt(
            semantic_signature_receipts,
            verification_receipts,
            provider_calls=provider_calls,
            verifier_calls=len(verification_receipts),
            total_tokens=total_tokens,
        )
        trace.record_diversity(diversity_receipt)

        selector_ids = {
            "first_verified": "first_verified_v1",
            "verifier_guided": "oracle_then_minimality_v1",
        }
        candidate_selection = str(
            policy.configuration.get("candidate_selection") or ""
        )
        if candidate_selection not in selector_ids:
            raise CandidatePortfolioExecutionViolation(
                "formal ACP forbids LLM/critic candidate selection"
            )
        selection = build_selection_receipt(
            verification_receipts,
            selection_policy=selector_ids[candidate_selection],
        )
        trace.record_selection(selection)
        execution_trace = trace.finalize(
            generation_receipts=generation_receipts,
            semantic_signature_receipts=semantic_signature_receipts,
            verification_receipts=verification_receipts,
            diversity_receipt=diversity_receipt,
            selection_receipt=selection,
            provider_calls=provider_calls,
            total_tokens=total_tokens,
            max_provider_calls=max_calls,
            max_tokens=max_tokens,
        )
        selected_id = selection["selected_candidate_id"]
        selected = next(
            (
                row for row in verification_receipts
                if row["candidate_id"] == selected_id
            ),
            None,
        )
        selected_patch_hash = (
            selected["patch_hash"]
            if selected is not None
            else hash_payload({"selected_candidate_id": None})
        )
        payload = {
            "schema_version": BLUE_EVALUATION_SCHEMA,
            "policy_instance_hash": policy.policy_instance_hash,
            "effective_policy_hash": policy.effective_policy_hash,
            "policy_hash": policy.policy_hash,
            "case_id": case_id,
            "run_seed": int(run_seed),
            "seed": int(run_seed),
            "descriptor_hash": failure.descriptor_hash,
            "portfolio_hash": self.portfolio.effective_portfolio_hash,
            "candidate_verifier_hash": self.verifier_hash,
            "semantic_signature_provider_hash": (
                self.semantic_signature_provider_hash
            ),
            "router_receipt": router_receipt,
            "allocator_receipt": allocator_receipt,
            "portfolio_control_receipt": portfolio_control_receipt,
            "memory_execution_plan_hash": memory_execution_plan_hash,
            "allocation_plan": plan.to_dict(),
            "allocation_plan_hash": plan.plan_hash,
            "candidate_provider_receipts": provider_receipts,
            "candidate_generation_receipts": generation_receipts,
            "candidate_semantic_signature_receipts": (
                semantic_signature_receipts
            ),
            "candidate_verification_receipts": verification_receipts,
            "portfolio_diversity_receipt": diversity_receipt,
            "selection_receipt": selection,
            "execution_trace": execution_trace,
            "selected_patch_hash": selected_patch_hash,
            "successful_patch_hash": (
                selected_patch_hash if selected is not None else ""
            ),
            "oracle_ok": selected is not None,
            "resource_usage": {
                "provider_calls": provider_calls,
                "llm_calls": provider_calls,
                "verifier_calls": len(verification_receipts),
                "input_tokens": sum(
                    row["input_tokens"] for row in generation_receipts
                ),
                "output_tokens": sum(
                    row["output_tokens"] for row in generation_receipts
                ),
                "wall_time_seconds": 0.0,
            },
        }
        payload["portfolio_execution_hash"] = hash_payload(payload)
        return payload
