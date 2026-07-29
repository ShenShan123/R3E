"""Sanitized ACP coverage and portfolio-aware Grounded Red plans.

The red adapter receives aggregate regions only.  Candidate patches, prompt
assets, provider/verifier receipts, successful-lens identities, and RAAM
evidence never cross this boundary.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from r3e.blue.portfolio.allocator import build_descriptor_cluster
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload


PORTFOLIO_COVERAGE_SCHEMA = "r3e-red-portfolio-coverage-v1"
PORTFOLIO_CHALLENGE_PLAN_SCHEMA = "r3e-red-portfolio-plan-v1"
PORTFOLIO_RED_AUTHORITY_SCHEMA = "r3e-red-portfolio-authority-v1"
PORTFOLIO_RED_OPERATORS = {
    "portfolio_bypass",
    "router_ambiguity",
    "specialist_deepening",
    "portfolio_conflict",
}
_FORBIDDEN_KEYS = {
    "prompt",
    "prompt_asset",
    "prompt_asset_path",
    "candidate_patch",
    "patch",
    "patch_payload",
    "blue_results",
    "candidate_provider_receipts",
    "candidate_generation_receipts",
    "candidate_semantic_signature_receipts",
    "candidate_verification_receipts",
    "selection_receipt",
    "provider_receipt",
    "verifier_receipt",
    "successful_lens_ids",
    "unique_solve_lens_ids",
    "source_episode_ids",
    "source_episode_hashes",
    "qualification_evidence",
    "control_delta",
    "reference_repair",
    "reference_patch",
    "hidden_target",
    "model_route_id",
}
_REGION_FIELDS = {
    "descriptor_cluster",
    "source_challenged_policy_hashes",
    "source_statistics_hashes",
    "challenge_count",
    "portfolio_attempts",
    "portfolio_successes",
    "success_rate_milli",
    "lens_collapse_runs",
    "high_duplicate_runs",
    "semantic_diversity_milli",
    "provider_calls",
    "verifier_calls",
    "total_tokens",
    "high_cost_low_gain",
    "region_kind",
    "region_hash",
}


class PortfolioChallengeViolation(RuntimeError):
    """Raised when ACP-derived red authority is stale, private, or forged."""


def portfolio_red_authority_hash() -> str:
    """Return the protocol/operator authority bound by the round toolchain."""
    return hash_payload({
        "schema_version": PORTFOLIO_COVERAGE_SCHEMA,
        "operators": sorted(PORTFOLIO_RED_OPERATORS),
    })


def _reject_private(value: Any) -> None:
    if isinstance(value, Mapping):
        leaked = _FORBIDDEN_KEYS & {
            str(key).lower() for key in value
        }
        if leaked:
            raise PortfolioChallengeViolation(
                f"private portfolio fields are forbidden: {sorted(leaked)}"
            )
        for item in value.values():
            _reject_private(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_private(item)


def _portfolio_binding(policy: PolicyState) -> dict[str, str]:
    binding = deepcopy(policy.candidate_portfolio_binding or {})
    required = {
        "portfolio_hash",
        "effective_portfolio_hash",
        "lens_registry_hash",
        "router_hash",
        "allocator_hash",
        "selector_hash",
        "semantic_signature_provider_hash",
    }
    if policy.schema_version != "r3e-policy-v3" or set(binding) != required:
        raise PortfolioChallengeViolation(
            "portfolio-aware red requires exact Policy V3 authority"
        )
    return binding


def _verified_statistics(row: Mapping[str, Any]) -> dict[str, Any] | None:
    statistics = row.get("portfolio_statistics")
    if not isinstance(statistics, Mapping):
        return None
    payload = deepcopy(dict(statistics))
    if (
        payload.get("schema_version")
        != "r3e-portfolio-challenge-statistics-v1"
        or payload.get("statistics_hash") != hash_payload({
            key: value for key, value in payload.items()
            if key != "statistics_hash"
        })
    ):
        raise PortfolioChallengeViolation(
            "archive portfolio statistics are not reconstructable"
        )
    return payload


def _compatible_descriptor(
    row: Mapping[str, Any],
    *,
    effective_portfolio_hash: str,
) -> dict[str, Any] | None:
    blue_results = row.get("blue_results")
    if not isinstance(blue_results, list) or not blue_results:
        return None
    if any(
        not isinstance(result, Mapping)
        or result.get("portfolio_hash") != effective_portfolio_hash
        for result in blue_results
    ):
        return None
    authority = row.get("grounded_authority_bundle")
    descriptor = (
        authority.get("failure_descriptor")
        if isinstance(authority, Mapping)
        else None
    )
    if not isinstance(descriptor, Mapping):
        raise PortfolioChallengeViolation(
            "portfolio archive row lacks Grounded FailureDescriptor"
        )
    try:
        return build_descriptor_cluster(descriptor)
    except Exception as exc:
        raise PortfolioChallengeViolation(
            "portfolio descriptor cluster is invalid"
        ) from exc


def build_portfolio_coverage_packet(
    policy: PolicyState,
    *,
    archive_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate compatible prior portfolio outcomes into public regions."""
    binding = _portfolio_binding(policy)
    grouped: dict[str, dict[str, Any]] = {}
    for raw in archive_rows:
        row = deepcopy(dict(raw))
        statistics = _verified_statistics(row)
        if statistics is None:
            continue
        cluster = _compatible_descriptor(
            row,
            effective_portfolio_hash=binding[
                "effective_portfolio_hash"
            ],
        )
        if cluster is None:
            continue
        cluster_hash = cluster["descriptor_cluster_hash"]
        aggregate = grouped.setdefault(cluster_hash, {
            "descriptor_cluster": cluster,
            "source_challenged_policy_hashes": set(),
            "source_statistics_hashes": set(),
            "challenge_count": 0,
            "portfolio_attempts": 0,
            "portfolio_successes": 0,
            "lens_collapse_runs": 0,
            "high_duplicate_runs": 0,
            "semantic_diversity_weighted": 0,
            "provider_calls": 0,
            "verifier_calls": 0,
            "total_tokens": 0,
        })
        if aggregate["descriptor_cluster"] != cluster:
            raise PortfolioChallengeViolation(
                "descriptor cluster hash collision"
            )
        attempts = len(statistics["seed_statistics"])
        aggregate["source_challenged_policy_hashes"].add(
            str(row.get("challenged_policy_hash") or "")
        )
        aggregate["source_statistics_hashes"].add(
            statistics["statistics_hash"]
        )
        aggregate["challenge_count"] += 1
        aggregate["portfolio_attempts"] += attempts
        aggregate["portfolio_successes"] += int(
            statistics["portfolio_successes"]
        )
        aggregate["lens_collapse_runs"] += int(
            statistics["lens_collapse_runs"]
        )
        aggregate["high_duplicate_runs"] += sum(
            int(seed["semantic_diversity_milli"]) <= 500
            or bool(seed["lens_collapse"])
            for seed in statistics["seed_statistics"]
        )
        aggregate["semantic_diversity_weighted"] += (
            int(statistics["semantic_diversity_milli"]) * attempts
        )
        for field in ("provider_calls", "verifier_calls", "total_tokens"):
            aggregate[field] += int(
                statistics["portfolio_cost"][field]
            )
    regions = []
    for cluster_hash in sorted(grouped):
        aggregate = grouped[cluster_hash]
        attempts = aggregate["portfolio_attempts"]
        successes = aggregate["portfolio_successes"]
        collapse = aggregate["lens_collapse_runs"]
        duplicates = aggregate["high_duplicate_runs"]
        if successes == 0:
            region_kind = "portfolio_blind_spot"
        elif collapse or duplicates:
            region_kind = "portfolio_collapse"
        else:
            region_kind = "portfolio_covered"
        body = {
            "descriptor_cluster": aggregate["descriptor_cluster"],
            "source_challenged_policy_hashes": sorted(
                value
                for value in aggregate[
                    "source_challenged_policy_hashes"
                ]
                if value
            ),
            "source_statistics_hashes": sorted(
                aggregate["source_statistics_hashes"]
            ),
            "challenge_count": aggregate["challenge_count"],
            "portfolio_attempts": attempts,
            "portfolio_successes": successes,
            "success_rate_milli": (
                successes * 1000 // attempts if attempts else 0
            ),
            "lens_collapse_runs": collapse,
            "high_duplicate_runs": duplicates,
            "semantic_diversity_milli": (
                aggregate["semantic_diversity_weighted"] // attempts
                if attempts else 0
            ),
            "provider_calls": aggregate["provider_calls"],
            "verifier_calls": aggregate["verifier_calls"],
            "total_tokens": aggregate["total_tokens"],
            "high_cost_low_gain": bool(
                attempts
                and successes == 0
                and aggregate["total_tokens"] > 0
            ),
            "region_kind": region_kind,
        }
        body["region_hash"] = hash_payload(body)
        regions.append(body)
    regions.sort(key=lambda region: region["region_hash"])
    packet = {
        "schema_version": PORTFOLIO_COVERAGE_SCHEMA,
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": (
            policy.effective_policy_hash
        ),
        "effective_portfolio_hash": binding[
            "effective_portfolio_hash"
        ],
        "portfolio_component_hashes": {
            key: binding[key]
            for key in (
                "lens_registry_hash",
                "router_hash",
                "allocator_hash",
                "selector_hash",
                "semantic_signature_provider_hash",
            )
        },
        "coverage_regions": regions,
        "coverage_summary": {
            "region_count": len(regions),
            "blind_spot_count": sum(
                region["region_kind"] == "portfolio_blind_spot"
                for region in regions
            ),
            "collapse_region_count": sum(
                region["region_kind"] == "portfolio_collapse"
                for region in regions
            ),
            "high_cost_low_gain_count": sum(
                region["high_cost_low_gain"] for region in regions
            ),
        },
        "allowed_operators": sorted(PORTFOLIO_RED_OPERATORS),
    }
    _reject_private(packet)
    packet["packet_hash"] = hash_payload(packet)
    return packet


def verify_portfolio_coverage_packet(
    packet: Mapping[str, Any],
    *,
    policy: PolicyState,
) -> dict[str, Any]:
    payload = deepcopy(dict(packet))
    required = {
        "schema_version",
        "challenged_policy_id",
        "challenged_policy_hash",
        "challenged_effective_policy_hash",
        "effective_portfolio_hash",
        "portfolio_component_hashes",
        "coverage_regions",
        "coverage_summary",
        "allowed_operators",
        "packet_hash",
    }
    if set(payload) != required:
        raise PortfolioChallengeViolation(
            "portfolio coverage packet fields mismatch"
        )
    binding = _portfolio_binding(policy)
    if (
        payload["schema_version"] != PORTFOLIO_COVERAGE_SCHEMA
        or payload["challenged_policy_id"] != policy.policy_id
        or payload["challenged_policy_hash"] != policy.policy_hash
        or payload["challenged_effective_policy_hash"]
        != policy.effective_policy_hash
        or payload["effective_portfolio_hash"]
        != binding["effective_portfolio_hash"]
        or payload["portfolio_component_hashes"] != {
            key: binding[key]
            for key in (
                "lens_registry_hash",
                "router_hash",
                "allocator_hash",
                "selector_hash",
                "semantic_signature_provider_hash",
            )
        }
        or payload["allowed_operators"]
        != sorted(PORTFOLIO_RED_OPERATORS)
    ):
        raise PortfolioChallengeViolation(
            "portfolio coverage packet authority mismatch"
        )
    regions = payload["coverage_regions"]
    if not isinstance(regions, list) or any(
        not isinstance(region, Mapping)
        or set(region) != _REGION_FIELDS
        or region["region_hash"] != hash_payload({
            key: value for key, value in region.items()
            if key != "region_hash"
        })
        for region in regions
    ):
        raise PortfolioChallengeViolation(
            "portfolio coverage region is invalid"
        )
    if [region["region_hash"] for region in regions] != sorted(
        region["region_hash"] for region in regions
    ):
        raise PortfolioChallengeViolation(
            "portfolio coverage regions are not canonical"
        )
    expected_summary = {
        "region_count": len(regions),
        "blind_spot_count": sum(
            region["region_kind"] == "portfolio_blind_spot"
            for region in regions
        ),
        "collapse_region_count": sum(
            region["region_kind"] == "portfolio_collapse"
            for region in regions
        ),
        "high_cost_low_gain_count": sum(
            bool(region["high_cost_low_gain"]) for region in regions
        ),
    }
    if payload["coverage_summary"] != expected_summary:
        raise PortfolioChallengeViolation(
            "portfolio coverage summary mismatch"
        )
    _reject_private(payload)
    if payload["packet_hash"] != hash_payload({
        key: value for key, value in payload.items()
        if key != "packet_hash"
    }):
        raise PortfolioChallengeViolation(
            "portfolio coverage packet hash mismatch"
        )
    return payload


def make_portfolio_challenge_plan(
    *,
    policy: PolicyState,
    packet: Mapping[str, Any],
    operator: str,
    poison_id: str,
    target_region_hashes: Iterable[str] = (),
) -> dict[str, Any]:
    verified = verify_portfolio_coverage_packet(packet, policy=policy)
    if operator not in PORTFOLIO_RED_OPERATORS:
        raise PortfolioChallengeViolation(
            "unknown portfolio red operator"
        )
    region_ids = {
        region["region_hash"] for region in verified["coverage_regions"]
    }
    targets = sorted(set(str(value) for value in target_region_hashes))
    if not set(targets) <= region_ids:
        raise PortfolioChallengeViolation(
            "portfolio challenge targets an unknown region"
        )
    if region_ids and not targets:
        raise PortfolioChallengeViolation(
            "portfolio challenge must bind a covered region"
        )
    plan = {
        "schema_version": PORTFOLIO_CHALLENGE_PLAN_SCHEMA,
        "poison_id": str(poison_id),
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "effective_portfolio_hash": verified[
            "effective_portfolio_hash"
        ],
        "capability_packet_hash": verified["packet_hash"],
        "operator": operator,
        "target_region_hashes": targets,
    }
    if not plan["poison_id"]:
        raise PortfolioChallengeViolation("portfolio plan poison_id missing")
    plan["plan_hash"] = hash_payload(plan)
    return plan


def materialize_portfolio_challenge(
    plan: Mapping[str, Any],
    poison: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply deterministic operator semantics to an adapter poison descriptor."""
    result = deepcopy(dict(poison))
    operator = str(plan.get("operator") or "")
    if operator not in PORTFOLIO_RED_OPERATORS:
        raise PortfolioChallengeViolation(
            "portfolio materializer operator mismatch"
        )
    if result.get("poison_id") != plan.get("poison_id"):
        raise PortfolioChallengeViolation(
            "portfolio materializer poison_id mismatch"
        )
    stress: dict[str, Any] = {
        "operator": operator,
        "target_region_hashes": list(
            plan.get("target_region_hashes") or []
        ),
    }
    if operator == "portfolio_bypass":
        stress.update({
            "descriptor_surface": "preserved",
            "root_cause_shift": "cross_specialist",
        })
    elif operator == "router_ambiguity":
        stress["symptom_classes"] = ["control", "temporal"]
    elif operator == "specialist_deepening":
        result["dependency_depth"] = int(
            result.get("dependency_depth") or 0
        ) + 1
        result["sequential_depth"] = int(
            result.get("sequential_depth") or 0
        ) + 1
        stress["depth_delta"] = 1
    else:
        result["composition_depth"] = int(
            result.get("composition_depth") or 1
        ) + 1
        stress["symptom_classes"] = [
            "control", "dataflow", "temporal"
        ]
    result.update({
        "portfolio_challenge_operator": operator,
        "portfolio_capability_packet_hash": plan[
            "capability_packet_hash"
        ],
        "portfolio_challenge_plan": deepcopy(dict(plan)),
        "portfolio_challenge_plan_hash": plan["plan_hash"],
        "portfolio_stress": stress,
    })
    return result


def verify_portfolio_challenge_execution(
    poison: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    packet: Mapping[str, Any],
    policy: PolicyState,
) -> dict[str, Any]:
    verified_packet = verify_portfolio_coverage_packet(
        packet, policy=policy
    )
    expected_plan = make_portfolio_challenge_plan(
        policy=policy,
        packet=verified_packet,
        operator=str(plan.get("operator") or ""),
        poison_id=str(plan.get("poison_id") or ""),
        target_region_hashes=plan.get("target_region_hashes") or [],
    )
    if dict(plan) != expected_plan:
        raise PortfolioChallengeViolation(
            "portfolio challenge plan is not reconstructable"
        )
    payload = deepcopy(dict(poison))
    operator = expected_plan["operator"]
    if (
        payload.get("poison_id") != expected_plan["poison_id"]
        or payload.get("challenged_policy_hash") != policy.policy_hash
        or payload.get("portfolio_challenge_operator") != operator
        or payload.get("portfolio_capability_packet_hash")
        != verified_packet["packet_hash"]
        or payload.get("portfolio_challenge_plan") != expected_plan
        or payload.get("portfolio_challenge_plan_hash")
        != expected_plan["plan_hash"]
    ):
        raise PortfolioChallengeViolation(
            "portfolio challenge execution binding mismatch"
        )
    stress = payload.get("portfolio_stress")
    if not isinstance(stress, Mapping) or (
        stress.get("operator") != operator
        or stress.get("target_region_hashes")
        != expected_plan["target_region_hashes"]
    ):
        raise PortfolioChallengeViolation(
            "portfolio challenge stress descriptor mismatch"
        )
    if operator == "portfolio_bypass":
        if set(stress) != {
            "operator",
            "target_region_hashes",
            "descriptor_surface",
            "root_cause_shift",
        } or (
            stress["descriptor_surface"] != "preserved"
            or stress["root_cause_shift"] != "cross_specialist"
        ):
            raise PortfolioChallengeViolation(
                "portfolio bypass semantics mismatch"
            )
    elif operator == "router_ambiguity":
        if set(stress) != {
            "operator", "target_region_hashes", "symptom_classes"
        } or stress["symptom_classes"] != ["control", "temporal"]:
            raise PortfolioChallengeViolation(
                "router ambiguity semantics mismatch"
            )
    elif operator == "specialist_deepening":
        if set(stress) != {
            "operator", "target_region_hashes", "depth_delta"
        } or stress["depth_delta"] != 1 or int(
            payload.get("dependency_depth") or 0
        ) < 1 or int(payload.get("sequential_depth") or 0) < 1:
            raise PortfolioChallengeViolation(
                "specialist deepening semantics mismatch"
            )
    elif (
        set(stress) != {
            "operator", "target_region_hashes", "symptom_classes"
        }
        or stress["symptom_classes"]
        != ["control", "dataflow", "temporal"]
        or int(payload.get("composition_depth") or 0) < 2
    ):
        raise PortfolioChallengeViolation(
            "portfolio conflict semantics mismatch"
        )
    _reject_private({
        "portfolio_stress": stress,
        "portfolio_challenge_plan": expected_plan,
    })
    return payload


def build_portfolio_red_authority(
    *,
    policy: PolicyState,
    packet: Mapping[str, Any],
    poisons: Iterable[Mapping[str, Any]],
    toolchain_fingerprint: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconstruct the complete ACP-6 red authority for one generation stage."""
    verified_packet = verify_portfolio_coverage_packet(
        packet, policy=policy
    )
    expected_authority_hash = portfolio_red_authority_hash()
    if (
        toolchain_fingerprint.get("schema_version")
        != "r3e-arena-composite-toolchain-v1"
        or toolchain_fingerprint.get("portfolio_red_authority_hash")
        != expected_authority_hash
    ):
        raise PortfolioChallengeViolation(
            "round toolchain does not bind portfolio red authority"
        )
    rows = [deepcopy(dict(row)) for row in poisons]
    poison_ids = [str(row.get("poison_id") or "") for row in rows]
    if any(not poison_id for poison_id in poison_ids) or (
        len(set(poison_ids)) != len(poison_ids)
    ):
        raise PortfolioChallengeViolation(
            "portfolio red poison ids are missing or duplicated"
        )
    verified = [
        verify_portfolio_challenge_execution(
            row,
            plan=row.get("portfolio_challenge_plan") or {},
            packet=verified_packet,
            policy=policy,
        )
        for row in rows
    ]
    record = {
        "schema_version": PORTFOLIO_RED_AUTHORITY_SCHEMA,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "effective_portfolio_hash": verified_packet[
            "effective_portfolio_hash"
        ],
        "capability_packet_hash": verified_packet["packet_hash"],
        "toolchain_authority_hash": expected_authority_hash,
        "candidate_count": len(verified),
        "candidate_poison_ids": [
            str(row["poison_id"]) for row in verified
        ],
        "candidate_payload_hashes": [
            str(row.get("poison_payload_hash") or "")
            for row in verified
        ],
        "challenge_plan_hashes": [
            str(row["portfolio_challenge_plan_hash"])
            for row in verified
        ],
        "operator_counts": {
            operator: sum(
                row["portfolio_challenge_operator"] == operator
                for row in verified
            )
            for operator in sorted(PORTFOLIO_RED_OPERATORS)
        },
    }
    record["authority_record_hash"] = hash_payload(record)
    return record
