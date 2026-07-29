"""Deterministic ACP semantic diversity relations and execution statistics."""
from __future__ import annotations

from copy import deepcopy
from itertools import combinations
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload

from .semantic_signature import SemanticPatchSignature


RELATION_SCHEMA = "r3e-semantic-patch-relation-v1"
DIVERSITY_SCHEMA = "r3e-portfolio-diversity-v1"
CHALLENGE_STATISTICS_SCHEMA = "r3e-portfolio-challenge-statistics-v1"
RELATIONS = {"semantic_duplicate", "near_duplicate", "orthogonal"}
NEAR_DUPLICATE_THRESHOLD_MILLI = 500


class PortfolioStatisticsViolation(RuntimeError):
    """Raised when semantic diversity statistics cannot be reconstructed."""


def _features(signature: SemanticPatchSignature) -> set[str]:
    result: set[str] = set()
    groups = (
        ("module", signature.changed_modules),
        ("block", signature.changed_blocks),
        ("node", signature.changed_ast_nodes),
        ("role", signature.changed_signal_roles),
        ("operator", signature.operator_classes),
    )
    for prefix, values in groups:
        result.update(f"{prefix}:{value}" for value in values)
    result.add(f"scope:{signature.patch_scope}")
    return result


def build_semantic_relation(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    left_id = str(left.get("candidate_id") or "")
    right_id = str(right.get("candidate_id") or "")
    if not left_id or not right_id or left_id == right_id:
        raise PortfolioStatisticsViolation(
            "semantic relation requires two distinct candidates"
        )
    first, second = sorted(
        (deepcopy(dict(left)), deepcopy(dict(right))),
        key=lambda row: row["candidate_id"],
    )
    left_signature = SemanticPatchSignature.from_dict(first["signature"])
    right_signature = SemanticPatchSignature.from_dict(second["signature"])
    left_features = _features(left_signature)
    right_features = _features(right_signature)
    union = left_features | right_features
    overlap_milli = (
        1000 * len(left_features & right_features) // len(union)
        if union else 1000
    )
    if (
        left_signature.normalized_ast_patch_hash
        == right_signature.normalized_ast_patch_hash
    ):
        relation = "semantic_duplicate"
    elif overlap_milli >= NEAR_DUPLICATE_THRESHOLD_MILLI:
        relation = "near_duplicate"
    else:
        relation = "orthogonal"
    payload = {
        "schema_version": RELATION_SCHEMA,
        "left_candidate_id": first["candidate_id"],
        "right_candidate_id": second["candidate_id"],
        "left_signature_hash": left_signature.signature_hash,
        "right_signature_hash": right_signature.signature_hash,
        "overlap_milli": overlap_milli,
        "relation": relation,
    }
    payload["relation_hash"] = hash_payload(payload)
    return payload


def build_portfolio_diversity_receipt(
    signature_receipts: list[Mapping[str, Any]],
    verification_receipts: list[Mapping[str, Any]],
    *,
    provider_calls: int,
    verifier_calls: int,
    total_tokens: int,
) -> dict[str, Any]:
    if len(signature_receipts) != len(verification_receipts):
        raise PortfolioStatisticsViolation(
            "signature/verification cardinality mismatch"
        )
    signatures = [deepcopy(dict(row)) for row in signature_receipts]
    verifications = [deepcopy(dict(row)) for row in verification_receipts]
    signature_ids = [row["candidate_id"] for row in signatures]
    verification_ids = [row["candidate_id"] for row in verifications]
    if signature_ids != verification_ids or len(signature_ids) != len(
        set(signature_ids)
    ):
        raise PortfolioStatisticsViolation(
            "semantic diversity candidate sequence mismatch"
        )
    relations = [
        build_semantic_relation(left, right)
        for left, right in combinations(signatures, 2)
    ]
    counts = {
        relation: sum(
            row["relation"] == relation for row in relations
        )
        for relation in sorted(RELATIONS)
    }
    relation_score = {
        "semantic_duplicate": 0,
        "near_duplicate": 500,
        "orthogonal": 1000,
    }
    semantic_diversity_milli = (
        sum(relation_score[row["relation"]] for row in relations)
        // len(relations)
        if relations else 1000
    )
    signature_by_id = {
        row["candidate_id"]: SemanticPatchSignature.from_dict(
            row["signature"]
        )
        for row in signatures
    }
    successful = [
        row for row in verifications if row.get("oracle_ok") is True
    ]
    lens_by_candidate = {
        row["candidate_id"]: str(row.get("lens_id") or "")
        for row in signatures
    }
    successful_lenses = sorted({
        lens_by_candidate[row["candidate_id"]]
        for row in successful
        if lens_by_candidate[row["candidate_id"]]
    })
    lens_collapse = any(
        row["relation"] == "semantic_duplicate"
        and lens_by_candidate[row["left_candidate_id"]]
        != lens_by_candidate[row["right_candidate_id"]]
        for row in relations
    )
    payload = {
        "schema_version": DIVERSITY_SCHEMA,
        "measurement_only": True,
        "retry_calls": 0,
        "candidate_ids": signature_ids,
        "semantic_signature_hashes": [
            signature_by_id[candidate_id].signature_hash
            for candidate_id in signature_ids
        ],
        "relations": relations,
        "semantic_unique_candidate_count": len({
            signature_by_id[candidate_id].normalized_ast_patch_hash
            for candidate_id in signature_ids
        }),
        "semantic_duplicate_pairs": counts["semantic_duplicate"],
        "near_duplicate_pairs": counts["near_duplicate"],
        "orthogonal_pairs": counts["orthogonal"],
        "semantic_diversity_milli": semantic_diversity_milli,
        "lens_collapse": lens_collapse,
        "candidate_success_count": len(successful),
        "unique_lens_successes": len(successful_lenses),
        "successful_lens_ids": successful_lenses,
        "unique_solve_lens_ids": (
            successful_lenses if len(successful) == 1 else []
        ),
        "co_solve_lens_ids": (
            successful_lenses if len(successful) > 1 else []
        ),
        "portfolio_cost": {
            "provider_calls": int(provider_calls),
            "verifier_calls": int(verifier_calls),
            "total_tokens": int(total_tokens),
        },
    }
    for field in ("provider_calls", "verifier_calls", "total_tokens"):
        if payload["portfolio_cost"][field] < 0:
            raise PortfolioStatisticsViolation(
                "portfolio cost must be non-negative"
            )
    payload["diversity_hash"] = hash_payload(payload)
    return payload


def build_challenge_portfolio_statistics(
    blue_results: list[Mapping[str, Any]],
) -> dict[str, Any]:
    seed_rows = []
    successful_lenses: set[str] = set()
    for result in blue_results:
        diversity = result.get("portfolio_diversity_receipt")
        if not isinstance(diversity, Mapping):
            raise PortfolioStatisticsViolation(
                "BlueEvaluation lacks portfolio diversity receipt"
            )
        successful_lenses.update(
            str(value) for value in diversity["successful_lens_ids"]
        )
        seed_rows.append({
            "seed": int(result["seed"]),
            "portfolio_success": bool(result["oracle_ok"]),
            "candidate_success_count": int(
                diversity["candidate_success_count"]
            ),
            "unique_lens_successes": int(
                diversity["unique_lens_successes"]
            ),
            "semantic_diversity_milli": int(
                diversity["semantic_diversity_milli"]
            ),
            "lens_collapse": bool(diversity["lens_collapse"]),
            "portfolio_cost": deepcopy(diversity["portfolio_cost"]),
            "diversity_hash": str(diversity["diversity_hash"]),
        })
    total_cost = {
        field: sum(
            int(row["portfolio_cost"][field]) for row in seed_rows
        )
        for field in ("provider_calls", "verifier_calls", "total_tokens")
    }
    payload = {
        "schema_version": CHALLENGE_STATISTICS_SCHEMA,
        "seed_statistics": seed_rows,
        "portfolio_successes": sum(
            row["portfolio_success"] for row in seed_rows
        ),
        "candidate_success_count": sum(
            row["candidate_success_count"] for row in seed_rows
        ),
        "unique_lens_successes": len(successful_lenses),
        "successful_lens_ids": sorted(successful_lenses),
        "semantic_diversity_milli": (
            sum(
                row["semantic_diversity_milli"] for row in seed_rows
            )
            // len(seed_rows)
            if seed_rows else 0
        ),
        "lens_collapse_runs": sum(
            row["lens_collapse"] for row in seed_rows
        ),
        "portfolio_cost": total_cost,
    }
    payload["statistics_hash"] = hash_payload(payload)
    return payload
