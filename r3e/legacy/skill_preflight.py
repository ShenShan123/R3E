"""Deprecated legacy preflight routing before semantic RTL LLM repair.

The preflight layer is intentionally conservative:
  - route to promoted registry policy before collecting evidence;
  - attach compact strategy hints from the skill registry;
  - reserve a deterministic/template hook, but do not apply unvalidated patches yet.

Default callers that do not pass a registry keep the historical repair behavior.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .skill_registry import infer_bug_family, load_registry, make_strategy_recall, route

_DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "configs" / "skills.json"


def _route_type(skill_id: str) -> str:
    if skill_id == "baseline":
        return "baseline"
    if skill_id == "r3e_enhanced_harness_default":
        return "default"
    return "specific"


def build_preflight_decision(
    case: dict,
    registry_path=None,
    *,
    requested_evidence_k: int = 1,
    requested_n_candidates: int = 1,
    enable_template_preflight: bool = False,
    formal_mode: bool = False,
) -> dict:
    """Return the effective preflight decision for a functional repair case."""
    if formal_mode:
        raise RuntimeError("legacy skill preflight is forbidden in formal_mode=True")
    if not registry_path:
        return {
            "enabled": False,
            "requested_policy": {
                "evidence_k": int(requested_evidence_k),
                "n_candidates": int(requested_n_candidates),
            },
            "effective_policy": {
                "evidence_k": int(requested_evidence_k),
                "n_candidates": int(requested_n_candidates),
            },
            "template_attempt": {
                "enabled": bool(enable_template_preflight),
                "status": "disabled",
                "hit": False,
            },
        }

    registry = load_registry(registry_path or _DEFAULT_REGISTRY, formal_mode=False)
    policy, skill_id = route(case, registry, formal_mode=False)
    family = infer_bug_family(case)
    strategy_context = make_strategy_recall(registry_path)(case) or ""
    effective = {
        "evidence_k": int(policy.get("evidence_k", requested_evidence_k)),
        "n_candidates": int(policy.get("n_candidates", requested_n_candidates)),
        "patch_scope": policy.get("patch_scope", "local_block"),
    }
    return {
        "enabled": True,
        "registry": str(registry_path),
        "family": family,
        "skill_id": skill_id,
        "route_type": _route_type(skill_id),
        "requested_policy": {
            "evidence_k": int(requested_evidence_k),
            "n_candidates": int(requested_n_candidates),
        },
        "effective_policy": effective,
        "has_strategy": bool(strategy_context),
        "strategy_context": strategy_context,
        "template_attempt": {
            "enabled": bool(enable_template_preflight),
            "status": "not_attempted",
            "hit": False,
        },
    }


def combine_recall_context(
    case: dict,
    preflight: dict,
    recall_fn: Callable[[dict], str] | None = None,
) -> str:
    """Merge registry strategy with legacy recall context when safe.

    When registry preflight is enabled, do not append caller-provided recall
    text.  That path is reserved for legacy/ablation runs; production preflight
    should use correctness-gated strategy hints and action/template hooks, not
    raw historical case prompt injection.
    """
    blocks = []
    strategy = preflight.get("strategy_context") or ""
    if strategy:
        blocks.append(strategy)
    if recall_fn is not None and not preflight.get("enabled"):
        caller_context = recall_fn(case) or ""
        if caller_context:
            blocks.append(caller_context)
    return "\n".join(blocks)


def try_deterministic_template_patch(case: dict, preflight: dict, pre_judge) -> dict:
    """Reserved high-confidence deterministic/template patch hook.

    No generalized template is enabled yet. The return shape is the stable
    interface future exact templates should implement:
      {hit: true, patch: {start_line, end_line, new_code}, template_id, rationale}
    """
    attempt = dict(preflight.get("template_attempt") or {})
    if not attempt.get("enabled"):
        attempt.update({"status": "disabled", "hit": False})
        return attempt
    attempt.update({
        "status": "reserved_no_registered_template",
        "hit": False,
        "family": preflight.get("family"),
        "skill_id": preflight.get("skill_id"),
    })
    return attempt
