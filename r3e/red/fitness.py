"""Separate red-search objectives plus optional scalar ranking."""
from __future__ import annotations

from typing import Any


LEARNABILITY_SCORES = {
    "reachable": 1.0,
    "weakly_reachable": 0.7,
    "unknown": 0.25,
    "unlearnable_or_budget_exceeded": 0.0,
}


def hardness(repair_successes: int, repair_attempts: int) -> float:
    if repair_attempts <= 0 or not 0 <= repair_successes <= repair_attempts:
        raise ValueError("invalid repair success/attempt counts")
    return 1.0 - repair_successes / repair_attempts


def hardness_class(repair_successes: int, repair_attempts: int) -> str:
    failure_ratio = hardness(repair_successes, repair_attempts)
    if repair_successes == 0:
        return "hard_residual"
    if failure_ratio >= 2 / 3:
        return "borderline_residual"
    if repair_successes < repair_attempts:
        return "mostly_covered"
    return "covered"


def score(
    *,
    valid: bool,
    hardness_value: float,
    novelty_value: float,
    learnability: str,
    normalized_cost: float,
) -> dict[str, Any]:
    if not valid:
        return {
            "valid": False,
            "hardness": hardness_value,
            "novelty": novelty_value,
            "learnability": learnability,
            "fitness": None,
        }
    learnability_value = LEARNABILITY_SCORES.get(learnability)
    if learnability_value is None:
        raise ValueError(f"unknown learnability label: {learnability}")
    fitness = (
        0.45 * hardness_value
        + 0.30 * novelty_value
        + 0.20 * learnability_value
        - 0.05 * max(0.0, normalized_cost)
    )
    return {
        "valid": True,
        "hardness": hardness_value,
        "novelty": novelty_value,
        "learnability": learnability,
        "learnability_score": learnability_value,
        "normalized_cost": normalized_cost,
        "fitness": fitness,
    }
