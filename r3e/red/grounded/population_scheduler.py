"""Deterministic GRD-6 Red Population scheduling authority.

The scheduler allocates already-frozen proposal intents to exactly one
generator lane.  Generator lanes may propose plans only; parser execution,
validation, minimization, and admission remain runner-owned authorities.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload, read_json


POPULATION_CONFIG_SCHEMA = "r3e-red-population-config-v1"
POPULATION_ASSIGNMENT_SCHEMA = "r3e-red-population-assignment-v1"
POPULATION_SCHEDULE_SCHEMA = "r3e-red-population-schedule-v1"
POPULATION_EXECUTION_SCHEMA = "r3e-red-population-execution-v1"
POPULATION_MODES = {"routed_population", "generalist_only"}
GENERATOR_ROLES = {
    "generalist",
    "family_specialist",
    "coverage_explorer",
    "hardness_escalator",
    "memory_adversary",
    "composition_specialist",
}
RUNNER_VALIDATOR_AUTHORITY = "runner_owned_grounded_execution"
RUNNER_MINIMIZER_AUTHORITY = "runner_owned_structural_minimizer"
_PROVIDER_FIELDS = {
    "provider_id",
    "provider_role",
    "model_id",
    "toolchain_fingerprint_hash",
    "budget_hash",
    "supported_specialist_kinds",
    "supported_family_prefixes",
    "max_assignments",
    "max_input_tokens",
    "max_output_tokens",
    "max_wall_time_ms",
}
_OPTIONAL_PROVIDER_FIELDS = {"minimum_wall_time_ms", "budget_mode"}
_FORBIDDEN_GENERATOR_FIELDS = {
    "validity",
    "admission_decision",
    "formal_proof_triplet",
    "oracle_verdict",
    "minimized_poison",
    "minimization_receipt",
    "validator_receipt",
}


class GroundedPopulationViolation(RuntimeError):
    """Raised when GRD-6 scheduling or provenance is not reconstructable."""


def _hashed(payload: Mapping[str, Any], field: str) -> bool:
    return payload.get(field) == hash_payload({
        key: value for key, value in payload.items() if key != field
    })


def _verify_provider(raw: Mapping[str, Any]) -> dict[str, Any]:
    provider = deepcopy(dict(raw))
    provider_fields = set(provider)
    if (
        not _PROVIDER_FIELDS <= provider_fields
        or provider_fields - _PROVIDER_FIELDS
        > _OPTIONAL_PROVIDER_FIELDS
    ):
        raise GroundedPopulationViolation(
            "population provider profile fields mismatch"
        )
    if (
        provider["provider_role"] not in GENERATOR_ROLES
        or any(
            not isinstance(provider[field], str) or not provider[field]
            for field in (
                "provider_id",
                "model_id",
                "toolchain_fingerprint_hash",
                "budget_hash",
            )
        )
        or not str(provider["toolchain_fingerprint_hash"]).startswith(
            "sha256:"
        )
        or not str(provider["budget_hash"]).startswith("sha256:")
    ):
        raise GroundedPopulationViolation(
            "population provider identity is invalid"
        )
    for field in (
        "supported_specialist_kinds",
        "supported_family_prefixes",
    ):
        values = provider[field]
        if (
            not isinstance(values, list)
            or values != sorted(set(str(value) for value in values))
        ):
            raise GroundedPopulationViolation(
                f"population provider {field} is not canonical"
            )
    for field in (
        "max_assignments",
        "max_input_tokens",
        "max_output_tokens",
        "max_wall_time_ms",
    ):
        value = provider[field]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
        ):
            raise GroundedPopulationViolation(
                f"population provider {field} must be positive"
            )
    if "minimum_wall_time_ms" in provider:
        value = provider["minimum_wall_time_ms"]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            or value > provider["max_wall_time_ms"]
        ):
            raise GroundedPopulationViolation(
                "population provider minimum_wall_time_ms is invalid"
            )
    if "budget_mode" in provider and provider["budget_mode"] not in {
        "difficulty_scaled",
        "fixed",
    }:
        raise GroundedPopulationViolation(
            "population provider budget_mode is invalid"
        )
    return provider


def verify_population_config(
    config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(config))
    required = {
        "schema_version",
        "scheduler_id",
        "scheduler_mode",
        "total_assignment_budget",
        "total_input_token_budget",
        "total_output_token_budget",
        "total_wall_time_budget_ms",
        "provider_profiles",
        "validator_authority",
        "minimizer_authority",
        "config_hash",
    }
    if (
        set(payload) != required
        or payload.get("schema_version") != POPULATION_CONFIG_SCHEMA
        or payload.get("scheduler_mode") not in POPULATION_MODES
        or not _hashed(payload, "config_hash")
        or payload.get("validator_authority")
        != RUNNER_VALIDATOR_AUTHORITY
        or payload.get("minimizer_authority")
        != RUNNER_MINIMIZER_AUTHORITY
        or not isinstance(payload.get("scheduler_id"), str)
        or not payload["scheduler_id"]
    ):
        raise GroundedPopulationViolation(
            "population config envelope is invalid"
        )
    for field in (
        "total_assignment_budget",
        "total_input_token_budget",
        "total_output_token_budget",
        "total_wall_time_budget_ms",
    ):
        value = payload[field]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
        ):
            raise GroundedPopulationViolation(
                f"population {field} must be positive"
            )
    providers = [
        _verify_provider(row) for row in payload["provider_profiles"]
    ]
    ids = [row["provider_id"] for row in providers]
    if (
        not providers
        or ids != sorted(ids)
        or len(ids) != len(set(ids))
        or not any(row["provider_role"] == "generalist" for row in providers)
    ):
        raise GroundedPopulationViolation(
            "population provider registry is invalid"
        )
    return {**payload, "provider_profiles": providers}


def load_population_config(path: str | Path) -> dict[str, Any]:
    return verify_population_config(read_json(path))


def population_protocol_hash(
    config: Mapping[str, Any],
) -> str:
    verified = verify_population_config(config)
    return hash_payload({
        "config_hash": verified["config_hash"],
        "assignment_schema": POPULATION_ASSIGNMENT_SCHEMA,
        "schedule_schema": POPULATION_SCHEDULE_SCHEMA,
        "execution_schema": POPULATION_EXECUTION_SCHEMA,
        "validator_authority": RUNNER_VALIDATOR_AUTHORITY,
        "minimizer_authority": RUNNER_MINIMIZER_AUTHORITY,
    })


def _provider_supports(
    provider: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> bool:
    specialist = str(intent["specialist_kind"])
    family = str(intent["family_id"])
    specialists = provider["supported_specialist_kinds"]
    prefixes = provider["supported_family_prefixes"]
    return (
        specialist in specialists
        and (
            not prefixes
            or any(family.startswith(prefix) for prefix in prefixes)
        )
    )


def _select_provider(
    *,
    config: Mapping[str, Any],
    intent: Mapping[str, Any],
    counts: Mapping[str, int],
) -> dict[str, Any]:
    providers = list(config["provider_profiles"])
    if config["scheduler_mode"] == "generalist_only":
        candidates = [
            row for row in providers
            if row["provider_role"] == "generalist"
        ]
    else:
        candidates = [
            row for row in providers
            if row["provider_role"] != "generalist"
            and _provider_supports(row, intent)
        ]
        if not candidates:
            candidates = [
                row for row in providers
                if row["provider_role"] == "generalist"
            ]
    available = [
        row for row in candidates
        if counts.get(row["provider_id"], 0) < row["max_assignments"]
    ]
    if not available:
        raise GroundedPopulationViolation(
            f"no population provider capacity for {intent['intent_id']}"
        )
    return sorted(
        available,
        key=lambda row: (
            0 if row["provider_role"] == "family_specialist" else 1,
            counts.get(row["provider_id"], 0),
            row["provider_id"],
        ),
    )[0]


def _assignment_budget(
    intent: Mapping[str, Any],
    provider: Mapping[str, Any],
) -> tuple[int, int, int]:
    if provider.get("budget_mode") == "fixed":
        return (
            provider["max_input_tokens"],
            provider["max_output_tokens"],
            provider["max_wall_time_ms"],
        )
    band = str(intent["difficulty_target"]["difficulty_band"])
    rank = {"D0": 0, "D1": 1, "D2": 2, "D3": 3, "D4": 4}[band]
    input_tokens = min(
        provider["max_input_tokens"], 2_048 + 256 * rank
    )
    output_tokens = min(
        provider["max_output_tokens"], 1_024 + 128 * rank
    )
    wall_time_ms = min(
        provider["max_wall_time_ms"],
        max(
            10_000 + 5_000 * rank,
            int(provider.get("minimum_wall_time_ms", 0)),
        ),
    )
    return input_tokens, output_tokens, wall_time_ms


def build_population_schedule(
    *,
    policy: PolicyState,
    proposal_plan: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    verified_config = verify_population_config(config)
    plan = deepcopy(dict(proposal_plan))
    if (
        plan.get("challenged_policy_hash") != policy.policy_hash
        or plan.get("challenged_effective_policy_hash")
        != policy.effective_policy_hash
        or plan.get("plan_hash") != hash_payload({
            key: value for key, value in plan.items()
            if key != "plan_hash"
        })
    ):
        raise GroundedPopulationViolation(
            "population proposal authority is invalid"
        )
    selected = {
        row["intent_id"]: row
        for row in plan["candidate_intents"]
        if row["intent_id"] in plan["selected_intent_ids"]
    }
    if len(selected) > verified_config["total_assignment_budget"]:
        raise GroundedPopulationViolation(
            "population assignment budget is exhausted"
        )
    counts: dict[str, int] = {}
    assignments = []
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "wall_time_ms": 0,
    }
    for index, intent_id in enumerate(
        plan["selected_intent_ids"], start=1
    ):
        intent = selected[intent_id]
        provider = _select_provider(
            config=verified_config,
            intent=intent,
            counts=counts,
        )
        input_tokens, output_tokens, wall_time_ms = (
            _assignment_budget(intent, provider)
        )
        assignment = {
            "schema_version": POPULATION_ASSIGNMENT_SCHEMA,
            "assignment_id": f"GRD6-{index:04d}",
            "intent_id": intent_id,
            "intent_hash": intent["intent_hash"],
            "challenged_policy_hash": policy.policy_hash,
            "provider_id": provider["provider_id"],
            "provider_role": provider["provider_role"],
            "model_id": provider["model_id"],
            "toolchain_fingerprint_hash": provider[
                "toolchain_fingerprint_hash"
            ],
            "budget_hash": provider["budget_hash"],
            "input_token_budget": input_tokens,
            "output_token_budget": output_tokens,
            "wall_time_budget_ms": wall_time_ms,
            "population_arm": verified_config["scheduler_mode"],
            "validator_authority": RUNNER_VALIDATOR_AUTHORITY,
            "minimizer_authority": RUNNER_MINIMIZER_AUTHORITY,
        }
        assignment["assignment_hash"] = hash_payload(assignment)
        assignments.append(assignment)
        counts[provider["provider_id"]] = (
            counts.get(provider["provider_id"], 0) + 1
        )
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["wall_time_ms"] += wall_time_ms
    if (
        totals["input_tokens"]
        > verified_config["total_input_token_budget"]
        or totals["output_tokens"]
        > verified_config["total_output_token_budget"]
        or totals["wall_time_ms"]
        > verified_config["total_wall_time_budget_ms"]
    ):
        raise GroundedPopulationViolation(
            "population aggregate resource budget is exhausted"
        )
    body = {
        "schema_version": POPULATION_SCHEDULE_SCHEMA,
        "scheduler_id": verified_config["scheduler_id"],
        "scheduler_mode": verified_config["scheduler_mode"],
        "config_hash": verified_config["config_hash"],
        "proposal_plan_hash": plan["plan_hash"],
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "validator_authority": RUNNER_VALIDATOR_AUTHORITY,
        "minimizer_authority": RUNNER_MINIMIZER_AUTHORITY,
        "assignments": assignments,
        "provider_assignment_counts": {
            key: counts[key] for key in sorted(counts)
        },
        "reserved_resources": totals,
        "ablation_factor_hash": hash_payload({
            "proposal_plan_hash": plan["plan_hash"],
            "scheduler_mode": verified_config["scheduler_mode"],
            "config_hash": verified_config["config_hash"],
        }),
    }
    return {**body, "schedule_hash": hash_payload(body)}


def verify_population_schedule(
    schedule: Mapping[str, Any],
    *,
    policy: PolicyState,
    proposal_plan: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    rebuilt = build_population_schedule(
        policy=policy,
        proposal_plan=proposal_plan,
        config=config,
    )
    payload = deepcopy(dict(schedule))
    if payload != rebuilt:
        raise GroundedPopulationViolation(
            "population schedule cannot be deterministically reconstructed"
        )
    return payload


def verify_population_candidates(
    schedule: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
    *,
    policy: PolicyState,
) -> dict[str, Any]:
    payload = deepcopy(dict(schedule))
    if (
        payload.get("schema_version") != POPULATION_SCHEDULE_SCHEMA
        or not _hashed(payload, "schedule_hash")
        or payload.get("challenged_policy_hash") != policy.policy_hash
    ):
        raise GroundedPopulationViolation(
            "population schedule envelope is invalid"
        )
    assignments = {
        row["assignment_id"]: row for row in payload["assignments"]
    }
    rows = [deepcopy(dict(row)) for row in candidates]
    by_assignment = {
        str(row.get("grounded_population_assignment_id") or ""): row
        for row in rows
    }
    if (
        set(by_assignment) != set(assignments)
        or len(by_assignment) != len(rows)
    ):
        raise GroundedPopulationViolation(
            "population candidates do not cover scheduled assignments"
        )
    bindings = []
    provider_usage: dict[str, dict[str, int]] = {}
    for assignment_id in sorted(assignments):
        assignment = assignments[assignment_id]
        if (
            assignment.get("assignment_hash") != hash_payload({
                key: value for key, value in assignment.items()
                if key != "assignment_hash"
            })
            or assignment.get("validator_authority")
            != RUNNER_VALIDATOR_AUTHORITY
            or assignment.get("minimizer_authority")
            != RUNNER_MINIMIZER_AUTHORITY
        ):
            raise GroundedPopulationViolation(
                "population assignment authority is invalid"
            )
        row = by_assignment[assignment_id]
        usage = dict(row.get("grounded_generation_usage") or {})
        if (
            _FORBIDDEN_GENERATOR_FIELDS & set(row)
            or row.get("grounded_population_assignment_hash")
            != assignment["assignment_hash"]
            or row.get("grounded_generator_provider_id")
            != assignment["provider_id"]
            or row.get("grounded_generator_role")
            != assignment["provider_role"]
            or row.get("grounded_population_arm")
            != assignment["population_arm"]
            or row.get("grounded_proposal_intent_id")
            != assignment["intent_id"]
            or row.get("challenged_policy_hash") != policy.policy_hash
            or row.get("model_id") != assignment["model_id"]
            or row.get("toolchain_fingerprint_hash")
            != assignment["toolchain_fingerprint_hash"]
            or row.get("budget_hash") != assignment["budget_hash"]
            or set(usage) != {
                "input_tokens",
                "output_tokens",
                "wall_time_ms",
            }
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in usage.values()
            )
            or usage["input_tokens"] > assignment["input_token_budget"]
            or usage["output_tokens"] > assignment["output_token_budget"]
            or usage["wall_time_ms"] > assignment["wall_time_budget_ms"]
        ):
            raise GroundedPopulationViolation(
                "population candidate provenance or budget mismatch"
            )
        current = provider_usage.setdefault(
            assignment["provider_id"],
            {
                "assignments": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "wall_time_ms": 0,
            },
        )
        current["assignments"] += 1
        for field in ("input_tokens", "output_tokens", "wall_time_ms"):
            current[field] += usage[field]
        bindings.append({
            "assignment_id": assignment_id,
            "assignment_hash": assignment["assignment_hash"],
            "intent_id": assignment["intent_id"],
            "poison_id": str(row.get("poison_id") or ""),
            "provider_id": assignment["provider_id"],
            "model_id": str(row["model_id"]),
            "toolchain_fingerprint_hash": str(
                row["toolchain_fingerprint_hash"]
            ),
            "command_hash": str(row["command_hash"]),
            "result_hash": str(row["result_hash"]),
            "usage": usage,
        })
    body = {
        "schema_version": POPULATION_EXECUTION_SCHEMA,
        "schedule_hash": payload["schedule_hash"],
        "proposal_plan_hash": payload["proposal_plan_hash"],
        "challenged_policy_hash": policy.policy_hash,
        "population_arm": payload["scheduler_mode"],
        "validator_authority": RUNNER_VALIDATOR_AUTHORITY,
        "minimizer_authority": RUNNER_MINIMIZER_AUTHORITY,
        "candidate_bindings": bindings,
        "provider_usage": {
            key: provider_usage[key] for key in sorted(provider_usage)
        },
    }
    return {**body, "execution_hash": hash_payload(body)}
