"""Competition adapter over the real R³E Blue Candidate Portfolio."""
from __future__ import annotations

from copy import deepcopy
import difflib
import json
import os
from pathlib import Path
from typing import Any

from r3e.arena.conformance import bind_adapter_output, make_toolchain_fingerprint
from r3e.blue.portfolio.candidate_executor import CandidatePortfolioExecutor
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.openai_provider import OpenAICompatibleCandidateProvider
from r3e.blue.portfolio.router import load_descriptor_router
from r3e.blue.portfolio.schema import CandidatePortfolio, build_candidate_portfolio_binding
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)

from ..config import CompetitionConfig, load_config
from .case_service import CaseCatalog, CaseDefinition
from .common import payload_hash
from .diagnosis_service import DiagnosisService
from .verification_service import CompetitionCandidateVerifier, VerificationService


class GuidedCandidateProvider:
    """Deterministic provider that proposes a checked-in minimal patch.

    It is still a Blue provider: it emits a proposal receipt, never a
    correctness decision.  The frozen patch is deliberately derived from the
    buggy source and is not the reference RTL.
    """

    requires_current_case_artifact = True
    toolchain_fingerprint = make_toolchain_fingerprint(
        adapter_id="r3e-aic-guided-minimal-patch",
        adapter_version="2",
        model_id="guided-minimal-patch-proposal",
        model_version="frozen-case-patch-v2",
        verifier_id="r3e-aic-competition-verifier",
        verifier_version="2",
        runtime_id="python-guided-provider",
        runtime_version="2",
    )

    def __init__(self, case: CaseDefinition):
        self.case = case
        self.minimal_patch = case.guided_candidate_source()

    def generate_candidate(
        self,
        *,
        policy: PolicyState,
        current_case_evidence: dict[str, Any],
        current_case_artifact: dict[str, Any],
        slot: dict[str, Any],
        prompt_asset: str,
        prompt_hash: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        slot_index = int(slot["slot_index"])
        replacement = self.minimal_patch if slot_index == 1 else current_case_artifact["buggy_rtl_source"]
        edit = (
            str(self.case.raw["demo_annotation"]["reference_edit"])
            if slot_index == 1
            else "No-op proposal retained so the oracle rejects this portfolio slot."
        )
        patch_payload = {
            "candidate_id": candidate_id,
            "slot_index": slot_index,
            "lens_id": slot["lens_id"],
            "replacement_rtl": replacement,
            "edit": edit,
        }
        body = {
            "candidate_id": candidate_id,
            "slot_index": slot_index,
            "lens_id": slot["lens_id"],
            "lens_hash": slot["lens_hash"],
            "candidate_seed": slot["candidate_seed"],
            "prompt_hash": prompt_hash,
            "current_case_evidence_hash": hash_payload(current_case_evidence),
            "current_case_artifact_hash": hash_payload(current_case_artifact),
            "raw_response_hash": hash_payload({
                "provider": "guided_minimal_patch",
                "case_id": self.case.case_id,
                "slot_index": slot_index,
                "replacement_sha256": payload_hash(replacement),
            }),
            "patch_payload": patch_payload,
            "patch_payload_hash": hash_payload(patch_payload),
            "input_tokens": 0,
            "output_tokens": 0,
        }
        return bind_adapter_output(
            body,
            "generate_blue_candidate",
            self.toolchain_fingerprint,
            budget_hash=hash_payload(policy.budgets),
            command_hash=hash_payload({
                "case_id": self.case.case_id,
                "slot_index": slot_index,
                "prompt_hash": prompt_hash,
            }),
        )


class RepairService:
    """Adapter/presentation facade; allocation and execution belong to R³E Core."""

    def __init__(
        self,
        repo_root: str | Path,
        output_root: str | Path,
        config: CompetitionConfig | None = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.output_root = Path(output_root).resolve()
        self.config = config or load_config(self.repo_root)
        self.catalog = CaseCatalog(self.repo_root)
        self.diagnosis = DiagnosisService(self.repo_root, self.output_root, self.config)
        self.verification = VerificationService(self.repo_root, self.output_root, self.config)
        paths = self.config.paths
        self.registry = load_lens_registry(
            self.repo_root / str(paths["lens_registry"]),
            project_root=self.repo_root,
        )
        self.router = load_descriptor_router(self.repo_root / str(paths["descriptor_router"]))
        self.portfolio = CandidatePortfolio.from_dict(
            json.loads((self.repo_root / str(paths["portfolio"])).read_text(encoding="utf-8"))
        )
        if set(self.config.lenses) != set(self.registry.lenses):
            raise ValueError("competition.yaml repair.lenses does not match the frozen lens registry")

    def _policy(self) -> PolicyState:
        base_path = self.repo_root / "configs" / "base_policy" / "frozen_base_policy_v3.json"
        base = PolicyState.from_dict(json.loads(base_path.read_text(encoding="utf-8")))
        return base.with_updates(
            policy_id="B_AIC_M2",
            parent_policy_id=base.policy_id,
            parent_policy_hash=base.policy_hash,
            created_round=1,
            status="active",
            configuration={
                **base.configuration,
                "n_candidates": self.config.candidate_budget,
                "candidate_selection": "verifier_guided",
                "patch_scope": "local_block",
            },
            candidate_portfolio_binding=build_candidate_portfolio_binding(self.portfolio),
        )

    def _live_provider(self) -> tuple[Any, dict[str, Any]]:
        provider_cfg = self.config.provider
        api_key_env = os.getenv("R3E_AIC_API_KEY_ENV", str(provider_cfg["api_key_env"]))
        base_url_env = os.getenv("R3E_AIC_BASE_URL_ENV", str(provider_cfg["base_url_env"]))
        provider_id = os.getenv("R3E_AIC_PROVIDER_ID", str(provider_cfg["default_provider_id"]))
        model_id = os.getenv("R3E_AIC_MODEL", str(provider_cfg["default_model"]))
        model_version = os.getenv("R3E_AIC_MODEL_VERSION", str(provider_cfg["default_model_version"]))
        config = OpenAICompatibleClientConfig(
            provider_id=provider_id,
            provider_version="competition-m2",
            endpoint_id="chat.completions",
            model_id=model_id,
            model_version=model_version,
            api_key_env=api_key_env,
            base_url_env=base_url_env,
            timeout_seconds=float(provider_cfg["timeout_seconds"]),
            maximum_output_tokens=int(provider_cfg["max_output_tokens"]),
            temperature=0.0,
            require_seed=True,
        )
        client = OpenAICompatibleJSONClient(config)
        readiness = client.readiness()
        if not readiness["ready"]:
            raise RuntimeError(
                "live provider is not ready; set the configured credential and base-url environment variables"
            )
        provider = OpenAICompatibleCandidateProvider(
            client,
            verifier_id="r3e-aic-competition-verifier",
            verifier_version="2",
        )
        return provider, {
            "id": provider_id,
            "kind": "live_model",
            "live_model_call": True,
            "readiness": readiness,
            "config_identity": config.public_identity,
        }

    @staticmethod
    def _diff(before: str, after: str) -> str:
        return "\n".join(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile="buggy.v", tofile="candidate.v", lineterm="",
        ))

    def _execute_portfolio(
        self,
        case: CaseDefinition,
        diagnosis: dict[str, Any],
        provider: Any,
        *,
        mode: str,
        run_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        verifier = CompetitionCandidateVerifier(
            self.verification,
            run_prefix=f"portfolio-{mode}-{run_id}",
        )
        executor = CandidatePortfolioExecutor(
            registry=self.registry,
            portfolio=self.portfolio,
            provider=provider,
            verifier=verifier,
            project_root=self.repo_root,
            router=self.router,
        )
        source = case.source("buggy")
        evaluation = executor.execute(
            policy=self._policy(),
            case={
                "case_id": case.case_id,
                "buggy_rtl_source": source,
                "buggy_rtl_hash": hash_payload(source),
                "top_module": case.top_module,
            },
            descriptor=diagnosis["grounded_descriptor"],
            run_seed=self.config.run_seed,
        )
        provider_receipts = evaluation["candidate_provider_receipts"]
        generation_receipts = {
            row["candidate_id"]: row for row in evaluation["candidate_generation_receipts"]
        }
        signatures = {
            row["candidate_id"]: row for row in evaluation["candidate_semantic_signature_receipts"]
        }
        receipt_verifications = {
            row["candidate_id"]: row for row in evaluation["candidate_verification_receipts"]
        }
        candidates: list[dict[str, Any]] = []
        for receipt in provider_receipts:
            patch = receipt["patch_payload"]
            runner_id = receipt["candidate_id"]
            slot_index = int(receipt["slot_index"])
            full = verifier.results.get(runner_id, {})
            scope = next(
                (row.get("evidence", {}) for row in full.get("stages", []) if row.get("name") == "scope"),
                {},
            )
            candidates.append({
                "id": f"C{slot_index + 1}",
                "runner_candidate_id": runner_id,
                "lens": receipt["lens_id"],
                "slot_index": slot_index,
                "status": "generated",
                "source_kind": "guided_minimal_patch" if mode == "demo" and slot_index == 1 else (
                    "guided_no_op_candidate" if mode == "demo" else "live_provider_proposal"
                ),
                "replacement_rtl": patch["replacement_rtl"],
                "replacement_sha256": payload_hash(patch["replacement_rtl"]),
                "edit": patch["edit"],
                "diff": self._diff(source, patch["replacement_rtl"]),
                "semantic_signature": signatures[runner_id]["signature"],
                "scope": scope,
                "candidate_receipt": receipt,
                "generation_receipt": generation_receipts[runner_id],
                "verification_receipt": receipt_verifications[runner_id],
                "verification": full,
            })
        return candidates, evaluation

    def generate(
        self,
        case_id: str,
        *,
        mode: str | None = None,
        run_id: str = "repair",
        candidate_budget: int | None = None,
    ) -> dict[str, Any]:
        requested_budget = self.config.candidate_budget if candidate_budget is None else int(candidate_budget)
        if requested_budget != self.config.candidate_budget:
            raise ValueError("candidate budget is controlled by competition.yaml")
        case = self.catalog.get(case_id)
        selected_mode = mode or self.config.mode
        diagnosis = self.diagnosis.diagnose(case_id, run_id=f"{run_id}-diagnosis")
        if selected_mode == "demo":
            provider: Any = GuidedCandidateProvider(case)
            provider_meta = {
                "id": "guided-minimal-patch",
                "kind": "guided_replay",
                "live_model_call": False,
                "note": "Guided replay emits a minimal patch from the buggy RTL; the reference RTL is oracle-only.",
            }
        elif selected_mode == "live":
            provider, provider_meta = self._live_provider()
        else:
            raise ValueError("mode must be demo or live")
        candidates, evaluation = self._execute_portfolio(
            case,
            diagnosis,
            provider,
            mode=selected_mode,
            run_id=run_id,
        )
        provider_meta = {
            **provider_meta,
            "candidate_budget": self.config.candidate_budget,
            "run_seed": self.config.run_seed,
        }
        return {
            "schema_version": "r3e-aic-repair-proposals-v2",
            "case_id": case_id,
            "mode": selected_mode,
            "policy_id": "B_AIC_M2",
            "candidate_budget": requested_budget,
            "diagnosis": diagnosis,
            "provider": provider_meta,
            "allocation": [
                {
                    "slot_index": slot["slot_index"],
                    "lens": slot["lens_id"],
                    "budget": 1,
                    "candidate_seed": slot["candidate_seed"],
                }
                for slot in evaluation["allocation_plan"]["slots"]
            ],
            "allocation_plan": evaluation["allocation_plan"],
            "descriptor_hash": diagnosis["failure_descriptor"]["descriptor_hash"],
            "portfolio_evaluation": evaluation,
            "authority": "R³E Blue Portfolio proposes; Scope Gate and EDA correctness gates select",
            "candidates": candidates,
        }
