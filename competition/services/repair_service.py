"""Proposal-only repair service with explicit live and guided-demo modes."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .case_service import CaseCatalog
from .common import payload_hash
from .diagnosis_service import DiagnosisService


class RepairService:
    def __init__(self, repo_root: str | Path, output_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.catalog = CaseCatalog(self.repo_root)
        self.diagnosis = DiagnosisService(self.repo_root, output_root)

    def generate(
        self,
        case_id: str,
        *,
        mode: str = "demo",
        run_id: str = "repair",
        candidate_budget: int = 3,
    ) -> dict[str, Any]:
        if candidate_budget != 3:
            raise ValueError("competition candidate budget is frozen at 3")
        case = self.catalog.get(case_id)
        diagnosis = self.diagnosis.diagnose(case_id, run_id=f"{run_id}-diagnosis")
        if mode == "demo":
            candidates = self._guided_candidates(case)
            provider = {
                "id": "frozen_reference_demo_provider",
                "kind": "guided_replay",
                "live_model_call": False,
                "note": "C2 is the frozen public reference RTL for a deterministic UI replay; this is not a model-effect result.",
            }
        elif mode == "live":
            candidates, provider = self._live_candidates(case, diagnosis)
        else:
            raise ValueError("mode must be demo or live")
        return {
            "schema_version": "r3e-aic-repair-proposals-v1",
            "case_id": case_id,
            "mode": mode,
            "candidate_budget": candidate_budget,
            "diagnosis": diagnosis,
            "provider": provider,
            "authority": "model proposes; competition verification service selects",
            "candidates": candidates,
        }

    def _guided_candidates(self, case) -> list[dict[str, Any]]:
        buggy = case.source("buggy")
        reference = case.source("reference")
        lens = str(case.raw["recommended_lens"])
        alternate = {
            "temporal": "control",
            "control": "dataflow",
            "dataflow": "temporal",
        }.get(lens, "generic")
        return [
            {
                "id": "C1",
                "lens": "generic",
                "status": "generated",
                "source_kind": "no_op_baseline_candidate",
                "replacement_rtl": buggy,
                "replacement_sha256": payload_hash(buggy),
                "edit": "No-op candidate retained to demonstrate oracle rejection.",
            },
            {
                "id": "C2",
                "lens": lens,
                "status": "generated",
                "source_kind": "frozen_reference_demo_candidate",
                "replacement_rtl": reference,
                "replacement_sha256": payload_hash(reference),
                "edit": str(case.raw["reference_edit"]),
            },
            {
                "id": "C3",
                "lens": alternate,
                "status": "generated",
                "source_kind": "no_op_alternate_candidate",
                "replacement_rtl": buggy,
                "replacement_sha256": payload_hash(buggy),
                "edit": "Alternate lens candidate retained to show multi-candidate rejection.",
            },
        ]

    def _live_candidates(self, case, diagnosis: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Call the repository's strict JSON provider only in explicit live mode."""
        from r3e.providers.openai_compatible import (
            OpenAICompatibleClientConfig,
            OpenAICompatibleJSONClient,
        )

        api_key_env = os.getenv("R3E_AIC_API_KEY_ENV", "OPENAI_API_KEY")
        base_url_env = os.getenv("R3E_AIC_BASE_URL_ENV", "OPENAI_BASE_URL")
        config = OpenAICompatibleClientConfig(
            provider_id=os.getenv("R3E_AIC_PROVIDER_ID", "openai-compatible"),
            provider_version="competition-v1",
            endpoint_id="chat.completions",
            model_id=os.getenv("R3E_AIC_MODEL", "gpt-4o-mini"),
            model_version=os.getenv("R3E_AIC_MODEL_VERSION", "configured"),
            api_key_env=api_key_env,
            base_url_env=base_url_env,
            timeout_seconds=float(os.getenv("R3E_AIC_TIMEOUT_SECONDS", "120")),
            maximum_output_tokens=int(os.getenv("R3E_AIC_MAX_OUTPUT_TOKENS", "4096")),
            temperature=0.0,
            require_seed=True,
        )
        client = OpenAICompatibleJSONClient(config)
        readiness = client.readiness()
        if not readiness["ready"]:
            raise RuntimeError(
                "live provider is not ready; set the configured credential and base-url environment variables"
            )
        source = case.source("buggy")
        candidates = []
        for index, lens in enumerate(("generic", case.raw["recommended_lens"], "dataflow")):
            response = client.complete_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Return strict JSON only with replacement_rtl and edit. "
                            "Propose a complete RTL candidate. Do not claim verification or correctness."
                        ),
                    },
                    {
                        "role": "user",
                        "content": __import__("json").dumps(
                            {
                                "lens": lens,
                                "case_id": case.case_id,
                                "diagnosis": diagnosis["evidence"],
                                "buggy_rtl": source,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                ],
                seed=index,
            )
            result = response["result"]
            replacement = result.get("replacement_rtl")
            edit = result.get("edit")
            if not isinstance(replacement, str) or not replacement.strip() or not isinstance(edit, str) or not edit.strip():
                raise RuntimeError("live provider returned an invalid proposal")
            candidates.append({
                "id": f"C{index + 1}",
                "lens": lens,
                "status": "generated",
                "source_kind": "live_provider_proposal",
                "replacement_rtl": replacement,
                "replacement_sha256": payload_hash(replacement),
                "edit": edit,
                "provider_receipt": {
                    "request_hash": response["request_hash"],
                    "raw_response_hash": response["raw_response_hash"],
                    "input_tokens": response["input_tokens"],
                    "output_tokens": response["output_tokens"],
                },
            })
        return candidates, {
            "id": config.provider_id,
            "kind": "live_model",
            "live_model_call": True,
            "readiness": readiness,
            "config_identity": config.public_identity,
        }
