"""Reasoning-only planner plus deterministic, formal-gated patch executor."""
from __future__ import annotations

import hashlib
from pathlib import Path

from semantic_repair_bench.correctness_gated_accumulation_curve import _base_route
from semantic_repair_bench.formal_gate import formal_judge
from semantic_repair_bench.functional_repair import apply_block
from semantic_repair_bench.skill_registry import infer_bug_family
from semantic_repair_bench.strategy_action_primitives import enumerate_strategy_patches


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_record(index: int, source: str, proposal: dict) -> dict:
    return {
        "cand": index,
        "source": source,
        "patch_range": [proposal.get("start_line"), proposal.get("end_line")],
        "primitive": proposal.get("primitive"),
        "primitive_delta": proposal.get("delta"),
    }


def blue_repair_formal_v2(
    poison: dict,
    work_dir: Path,
    *,
    registry: list[dict],
    accumulator,
    use_accumulated_templates: bool,
    formal_timeout: int,
    candidate_budget: int | None = None,
) -> dict:
    """Execute only hash-bound primitives; the LLM has no patch authority."""
    policy, skill_id = _base_route(poison, registry)
    template = accumulator.template_for(poison) if accumulator and use_accumulated_templates else None
    shadow_template = accumulator.shadow_template_for(poison) if accumulator and use_accumulated_templates else None
    if template:
        action_policy = template.get("action_policy") or {}
        if (
            action_policy.get("planner_authority") != "select_repair_primitive_only"
            or action_policy.get("executor_authority") != "deterministic_patch_generation_only"
            or action_policy.get("llm_patch_generation_allowed") is not False
        ):
            raise RuntimeError("active strategy violates reasoning-only patch authority")
        policy.update(action_policy)
    del candidate_budget  # An LLM candidate budget cannot grant patch authority.

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[dict] = []
    residuals: list[dict] = []
    win = None

    if template and policy.get("strategy_primitives"):
        for proposal in enumerate_strategy_patches(poison["buggy"], policy):
            cr = _candidate_record(len(candidates), "strategy_primitive", proposal)
            try:
                candidate_dir = work_dir / f"primitive_{len(candidates):03d}"
                patched = apply_block(
                    poison["buggy"], int(proposal["start_line"]), int(proposal["end_line"]),
                    proposal["new_code"], candidate_dir / Path(poison["buggy"]).name,
                )
                judge = formal_judge(
                    poison["golden"], poison.get("deps", []), patched, poison["top"],
                    work_dir / f"primitive_judge_{len(candidates):03d}", timeout=formal_timeout,
                )
                cr.update({
                    "formal_equiv": judge.equiv, "formal_proven": judge.proven,
                    "formal_total": judge.total, "yosys_exit": judge.yosys_exit,
                    "formal_error": judge.err, "patched_rtl": str(patched),
                    "patch_hash": _sha(patched),
                    "proof_method": judge.proof_method,
                    "proof_artifact": judge.proof_artifact,
                })
            except Exception as exc:  # noqa: BLE001
                cr["apply_error"] = str(exc)
            candidates.append(cr)
            if cr.get("formal_equiv"):
                win = cr
                break
            residuals.append({
                "candidate_index": cr["cand"],
                "primitive": cr.get("primitive"),
                "patch_hash": cr.get("patch_hash"),
                "formal_proven": cr.get("formal_proven"),
                "formal_total": cr.get("formal_total"),
                "formal_error": cr.get("formal_error") or cr.get("apply_error") or "non-equivalent",
                "controller_action": "advance_to_next_untried_primitive_candidate",
            })

    chosen = win or (candidates[-1] if candidates else {})
    return {
        "repaired": win is not None,
        "pass_at_1": bool(candidates[:1] and candidates[0].get("formal_equiv")),
        "pass_at_3": any(row.get("formal_equiv") for row in candidates[:3]),
        "skill_id": skill_id,
        "family": infer_bug_family(poison),
        "used_accumulated_template": bool(template),
        "template_id": template.get("template_id") if template else None,
        "shadow_template_hit": bool(shadow_template),
        "shadow_template_id": shadow_template.get("template_id") if shadow_template else None,
        "template_stage": accumulator.template_stage if accumulator else "none",
        "effective_policy": policy,
        "planner_authority": "reasoning_only",
        "patch_authority": "deterministic_primitive_executor",
        "llm_patch_generation_allowed": False,
        "n_candidates_tried": len(candidates),
        "n_strategy_primitive_trials": sum(row.get("source") == "strategy_primitive" for row in candidates),
        "n_llm_revisions": 0,
        "formal_residuals": residuals,
        "patch_range": chosen.get("patch_range"),
        "rationale": chosen.get("rationale", ""),
        "candidates": candidates,
    }
