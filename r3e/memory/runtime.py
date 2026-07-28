"""Formal deterministic RAAM retrieval/reactivation/plan pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.events import EventLogger

from .activation_guard import ActivationGuard
from .bank_store import ActiveBankStore
from .descriptor import build_failure_descriptor
from .plan_compiler import MemoryAwarePlanCompiler
from .retriever import MemoryRetriever
from .schema import ExecutionPlan, RuntimeContext
from .execution_trace import ExecutionTraceRecorder


class MemoryRuntime:
    """Compile a bounded plan without calling an LLM or constructing prompt text."""

    def __init__(
        self,
        *,
        bank_store: ActiveBankStore,
        retriever: MemoryRetriever,
        activation_guard: ActivationGuard,
        event_logger: EventLogger | None = None,
    ):
        self.bank_store = bank_store
        self.retriever = retriever
        self.activation_guard = activation_guard
        self.event_logger = event_logger

    def compile_plan(
        self,
        *,
        observable_failure: Mapping[str, Any],
        active_policy: PolicyState,
        runtime_context: RuntimeContext,
        round_id: str = "",
    ) -> ExecutionPlan:
        bank = self.bank_store.load_for_policy(active_policy)
        descriptor = build_failure_descriptor(observable_failure)
        matches = self.retriever.retrieve(descriptor, bank, top_k=3)
        decision = self.activation_guard.reactivate(
            matches=matches,
            active_policy=active_policy,
            active_bank=bank,
            runtime_context=runtime_context,
        )
        plan = MemoryAwarePlanCompiler(
            self.bank_store.memory_store, bank
        ).compile(
            base_policy=active_policy,
            reactivation=decision,
        )
        if self.event_logger:
            self.event_logger.emit(
                "memory",
                "memory_reactivation_decided",
                round_id=round_id,
                effective_policy_hash=active_policy.effective_policy_hash,
                active_memory_bank_hash=bank.bank_hash,
                descriptor_hash=descriptor.descriptor_hash,
                matched_memory_ids=[match.memory_id for match in matches],
                activated_memory_ids=list(decision.activated_memory_ids),
                abstained=decision.abstained,
                reason_code=decision.reason_code,
                reactivation_decision_hash=decision.decision_hash,
                execution_plan_hash=plan.plan_hash,
            )
        return plan

    def repair_one(
        self,
        *,
        case: Mapping[str, Any],
        work_dir: str | Path,
        observable_failure: Mapping[str, Any],
        active_policy: PolicyState,
        runtime_context: RuntimeContext,
        executor: Callable[..., Mapping[str, Any]],
        round_id: str = "",
    ) -> dict[str, Any]:
        """Compile once, then execute one authoritative memory-aware arm.

        The executor must bind its result to both the effective policy and
        execution-plan hashes.  This keeps model/tool implementations outside
        the authority layer while preventing silent fallback to a default plan.
        """
        plan = self.compile_plan(
            observable_failure=observable_failure,
            active_policy=active_policy,
            runtime_context=runtime_context,
            round_id=round_id,
        )
        result = dict(
            # The recorder owns observable operation invocation.  A returned
            # plan hash without these calls is not execution evidence.
            executor(
                case=dict(case),
                work_dir=Path(work_dir),
                policy=active_policy,
                execution_plan=plan,
                trace_recorder=(
                    trace_recorder := ExecutionTraceRecorder(plan)
                ),
            )
        )
        if result.get("effective_policy_hash") != active_policy.effective_policy_hash:
            raise RuntimeError("memory-aware executor returned wrong policy hash")
        if result.get("execution_plan_hash") != plan.plan_hash:
            raise RuntimeError("memory-aware executor returned wrong plan hash")
        if int(result.get("memory_prompt_tokens") or 0) != 0:
            raise RuntimeError("memory-aware executor injected memory prompt tokens")
        trace = trace_recorder.finalize()
        claimed_trace_hash = str(result.get("execution_trace_hash") or "")
        if claimed_trace_hash and claimed_trace_hash != trace["trace_hash"]:
            raise RuntimeError("memory-aware executor trace hash mismatch")
        result["execution_trace"] = trace
        result["execution_trace_hash"] = trace["trace_hash"]
        result["activated_memory_ids"] = list(plan.activated_memory_ids)
        result["memory_token_cost"] = plan.memory_token_cost
        return result
