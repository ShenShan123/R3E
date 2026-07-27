"""Hash-bound learnability teacher protocol with separated budgets."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload


LEARNABILITY_SCHEMA_VERSION = "r3e-learnability-v1"
LEARNABILITY_LABELS = {
    "reachable",
    "weakly_reachable",
    "unknown",
    "unlearnable_or_budget_exceeded",
}
TEACHER_MODES = {
    "same_model_expanded",
    "stronger_model",
    "population_expanded",
}


class LearnabilityViolation(RuntimeError):
    """Raised when teacher evidence is incomplete or budget-leaking."""


def _positive_int_budget(raw: Any, *, field: str) -> dict[str, int]:
    if not isinstance(raw, Mapping) or not raw:
        raise LearnabilityViolation(f"{field} must be a non-empty budget object")
    result: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise LearnabilityViolation(f"{field} contains an invalid key")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise LearnabilityViolation(f"{field}.{key} must be an integer") from exc
        if parsed <= 0:
            raise LearnabilityViolation(f"{field}.{key} must be positive")
        result[key] = parsed
    return result


def _assert_expanded(
    primary: dict[str, int], teacher: dict[str, int], *, mode: str
) -> None:
    common = set(primary) & set(teacher)
    if not common:
        raise LearnabilityViolation("teacher budget does not bind primary budget dimensions")
    if any(teacher[key] < primary[key] for key in common):
        raise LearnabilityViolation("teacher budget cannot be lower than primary budget")
    expanded = any(teacher[key] > primary[key] for key in common)
    if mode in {"same_model_expanded", "population_expanded"} and not expanded:
        raise LearnabilityViolation("teacher budget must strictly expand primary budget")


def validate_learnability_result(
    policy: PolicyState,
    poison: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and canonicalize one adapter-supplied teacher probe."""
    if not isinstance(raw, Mapping):
        raise LearnabilityViolation("learnability probe must return an object")
    allowed = {
        "label",
        "challenged_policy_hash",
        "teacher_mode",
        "teacher_budget",
        "attempts",
        "successes",
        "budget_exhausted",
        "evidence",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise LearnabilityViolation(f"undeclared learnability fields: {sorted(unknown)}")
    label = str(raw.get("label") or "")
    if label not in LEARNABILITY_LABELS:
        raise LearnabilityViolation(f"invalid learnability label: {label}")
    challenged_hash = str(raw.get("challenged_policy_hash") or "")
    if challenged_hash != policy.policy_hash:
        raise LearnabilityViolation("learnability probe is bound to the wrong policy")
    if poison.get("challenged_policy_hash") != policy.policy_hash:
        raise LearnabilityViolation("poison is not bound to learnability policy")
    mode = str(raw.get("teacher_mode") or "")
    if mode not in TEACHER_MODES:
        raise LearnabilityViolation(f"invalid teacher mode: {mode}")
    primary = _positive_int_budget(policy.budgets, field="primary_budget")
    teacher = _positive_int_budget(raw.get("teacher_budget"), field="teacher_budget")
    _assert_expanded(primary, teacher, mode=mode)
    try:
        attempts = int(raw.get("attempts"))
        successes = int(raw.get("successes"))
    except (TypeError, ValueError) as exc:
        raise LearnabilityViolation("teacher attempts/successes must be integers") from exc
    if attempts <= 0 or not 0 <= successes <= attempts:
        raise LearnabilityViolation("invalid teacher attempts/successes")
    exhausted = bool(raw.get("budget_exhausted"))
    if label in {"reachable", "weakly_reachable"} and successes < 1:
        raise LearnabilityViolation("reachable label requires a teacher success")
    if label == "reachable" and mode != "same_model_expanded":
        raise LearnabilityViolation("reachable requires same-model expanded-budget evidence")
    if label == "weakly_reachable" and mode not in {
        "stronger_model",
        "population_expanded",
    }:
        raise LearnabilityViolation("weakly_reachable requires stronger teacher evidence")
    if label == "unknown" and (successes or exhausted):
        raise LearnabilityViolation("unknown requires no success and an incomplete teacher search")
    if label == "unlearnable_or_budget_exceeded" and (successes or not exhausted):
        raise LearnabilityViolation(
            "unlearnable_or_budget_exceeded requires exhausted zero-success evidence"
        )
    evidence = deepcopy(raw.get("evidence") or {})
    if not isinstance(evidence, (dict, list)):
        raise LearnabilityViolation("learnability evidence must be structured")
    result = {
        "schema_version": LEARNABILITY_SCHEMA_VERSION,
        "label": label,
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "poison_id": str(poison.get("poison_id") or ""),
        "primary_budget": primary,
        "primary_budget_hash": hash_payload(primary),
        "teacher_mode": mode,
        "teacher_budget": teacher,
        "teacher_budget_hash": hash_payload(teacher),
        "attempts": attempts,
        "successes": successes,
        "budget_exhausted": exhausted,
        "evidence": evidence,
    }
    if not result["poison_id"]:
        raise LearnabilityViolation("learnability result must bind poison_id")
    result["evidence_hash"] = hash_payload(evidence)
    result["result_hash"] = hash_payload(result)
    return result


def verify_learnability_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a persisted canonical result without rerunning the teacher."""
    payload = deepcopy(dict(result))
    if payload.get("schema_version") != LEARNABILITY_SCHEMA_VERSION:
        raise LearnabilityViolation("learnability schema mismatch")
    if payload.get("label") not in LEARNABILITY_LABELS:
        raise LearnabilityViolation("learnability label mismatch")
    mode = str(payload.get("teacher_mode") or "")
    if mode not in TEACHER_MODES:
        raise LearnabilityViolation("learnability teacher mode mismatch")
    primary = _positive_int_budget(
        payload.get("primary_budget"), field="primary_budget"
    )
    teacher = _positive_int_budget(
        payload.get("teacher_budget"), field="teacher_budget"
    )
    _assert_expanded(primary, teacher, mode=mode)
    try:
        attempts = int(payload.get("attempts"))
        successes = int(payload.get("successes"))
    except (TypeError, ValueError) as exc:
        raise LearnabilityViolation("teacher attempts/successes must be integers") from exc
    if attempts <= 0 or not 0 <= successes <= attempts:
        raise LearnabilityViolation("invalid persisted teacher attempts/successes")
    label = str(payload["label"])
    exhausted = bool(payload.get("budget_exhausted"))
    if label in {"reachable", "weakly_reachable"} and successes < 1:
        raise LearnabilityViolation("persisted reachable result has no success")
    if label == "reachable" and mode != "same_model_expanded":
        raise LearnabilityViolation("persisted reachable teacher mode mismatch")
    if label == "weakly_reachable" and mode not in {
        "stronger_model",
        "population_expanded",
    }:
        raise LearnabilityViolation("persisted weak teacher mode mismatch")
    if label == "unknown" and (successes or exhausted):
        raise LearnabilityViolation("persisted unknown semantics mismatch")
    if label == "unlearnable_or_budget_exceeded" and (successes or not exhausted):
        raise LearnabilityViolation("persisted exhausted semantics mismatch")
    if payload.get("primary_budget_hash") != hash_payload(payload.get("primary_budget")):
        raise LearnabilityViolation("primary budget hash mismatch")
    if payload.get("teacher_budget_hash") != hash_payload(payload.get("teacher_budget")):
        raise LearnabilityViolation("teacher budget hash mismatch")
    if payload.get("evidence_hash") != hash_payload(payload.get("evidence")):
        raise LearnabilityViolation("learnability evidence hash mismatch")
    body = {key: value for key, value in payload.items() if key != "result_hash"}
    if payload.get("result_hash") != hash_payload(body):
        raise LearnabilityViolation("learnability result hash mismatch")
    return payload
