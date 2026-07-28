"""Frozen paired-replay promotion gate for whole policies."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from typing import Any

from r3e.arena.manifests import verify_manifest
from r3e.protocol.hashing import hash_payload
from r3e.protocol.hashing import atomic_write_json, read_json

from .schema import PolicyState


class PromotionViolation(RuntimeError):
    """Raised when paired evidence is incomplete, unbalanced, or leaked."""


DEFAULT_THRESHOLDS = {
    "min_prior_failure_recovery": 2,
    "min_target_recovery_ratio": 0.50,
    "min_covered_designs": 2,
    "non_target_epsilon": 0.02,
    "max_cost_ratio": 1.5,
}
REQUIRED_PROVENANCE_HASHES = {
    "residual_manifest_hash",
    "adaptation_manifest_hash",
    "target_manifest_hash",
    "non_target_manifest_hash",
    "paired_result_hash",
    "toolchain_fingerprint_hash",
    "run_context_hash",
}
PROMOTION_BUNDLE_SCHEMA_VERSION = "r3e-policy-promotion-bundle-v1"


def _verify_rows_against_manifests(
    rows: list[dict[str, Any]],
    target_manifest: dict[str, Any],
    non_target_manifest: dict[str, Any],
) -> None:
    memberships = {
        "target": {
            (
                str(row.get("case_id") or row.get("poison_id") or ""),
                str(row.get("design") or row.get("design_id") or ""),
            )
            for row in target_manifest["rows"]
        },
        "non_target": {
            (
                str(row.get("case_id") or row.get("poison_id") or ""),
                str(row.get("design") or row.get("design_id") or ""),
            )
            for row in non_target_manifest["rows"]
        },
    }
    observed = {"target": set(), "non_target": set()}
    for row in rows:
        split = str(row.get("split") or "")
        key = (
            str(row.get("case_id") or ""),
            str(row.get("design") or ""),
        )
        if split not in memberships or key not in memberships[split]:
            raise PromotionViolation(
                "paired replay row is not a member of its frozen manifest"
            )
        observed[split].add(key)
    for split in ("target", "non_target"):
        if observed[split] != memberships[split]:
            raise PromotionViolation(
                f"paired replay does not cover the frozen {split} manifest"
            )


def _provenance_complete(provenance: dict[str, Any]) -> bool:
    if not str(provenance.get("round_id") or ""):
        return False
    code_version = str(provenance.get("code_commit_sha") or "")
    if not code_version or code_version.lower() == "unknown":
        return False
    for field in REQUIRED_PROVENANCE_HASHES:
        value = str(provenance.get(field) or "")
        if not value.startswith("sha256:") or len(value) != 71:
            return False
    toolchain = provenance.get("toolchain_fingerprint")
    return isinstance(toolchain, dict) and bool(toolchain)


def _paired(rows: list[dict[str, Any]]) -> dict[str, dict[tuple[str, int], dict[str, dict]]]:
    grouped: dict[str, dict[tuple[str, int], dict[str, dict]]] = {
        "target": defaultdict(dict),
        "non_target": defaultdict(dict),
    }
    for row in rows:
        split = str(row.get("split") or "")
        arm = str(row.get("arm") or "")
        case_id = str(row.get("case_id") or "")
        seed = int(row.get("seed") or 0)
        if split not in grouped or arm not in {"parent", "candidate"} or not case_id:
            raise PromotionViolation("invalid paired replay row")
        key = (case_id, seed)
        if arm in grouped[split][key]:
            raise PromotionViolation(f"duplicate paired replay row: {split}/{key}/{arm}")
        grouped[split][key][arm] = row
    for split, pairs in grouped.items():
        if not pairs:
            raise PromotionViolation(f"empty replay split: {split}")
        for key, arms in pairs.items():
            if set(arms) != {"parent", "candidate"}:
                raise PromotionViolation(f"unpaired replay row: {split}/{key}")
            parent = arms["parent"]
            candidate = arms["candidate"]
            for field in ("design", "seed", "case_id", "model_id", "budget_hash", "verifier_hash"):
                if parent.get(field) != candidate.get(field):
                    raise PromotionViolation(f"paired {field} mismatch: {split}/{key}")
    target_designs = {
        str(arms["parent"].get("design") or "") for arms in grouped["target"].values()
    }
    non_target_designs = {
        str(arms["parent"].get("design") or "") for arms in grouped["non_target"].values()
    }
    if "" in target_designs | non_target_designs:
        raise PromotionViolation("every replay row must bind a design")
    if target_designs & non_target_designs:
        raise PromotionViolation("target and non-target designs overlap")
    return grouped


def _wilson(hits: int, total: int, z: float = 1.96) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    p = hits / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _exact_mcnemar(wins: int, losses: int) -> float:
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(wins, losses) + 1))
    return min(1.0, 2.0 * tail / (2 ** discordant))


def decide_policy_promotion(
    parent: PolicyState,
    candidate: PolicyState,
    validation_rows: list[dict[str, Any]],
    *,
    validation_manifest_hash: str,
    thresholds: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if candidate.parent_policy_id != parent.policy_id:
        raise PromotionViolation("candidate parent id is stale")
    if candidate.parent_policy_hash != parent.policy_hash:
        raise PromotionViolation("candidate parent hash is stale")
    gates = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    pairs = _paired(validation_rows)

    def metrics(split: str) -> dict[str, Any]:
        values = list(pairs[split].values())
        parent_hits = sum(bool(item["parent"].get("oracle_ok")) for item in values)
        child_hits = sum(bool(item["candidate"].get("oracle_ok")) for item in values)
        wins = sum(
            not bool(item["parent"].get("oracle_ok"))
            and bool(item["candidate"].get("oracle_ok"))
            for item in values
        )
        losses = sum(
            bool(item["parent"].get("oracle_ok"))
            and not bool(item["candidate"].get("oracle_ok"))
            for item in values
        )
        total = len(values)
        return {
            "parent_hits": parent_hits,
            "candidate_hits": child_hits,
            "total": total,
            "parent_rate": parent_hits / total,
            "candidate_rate": child_hits / total,
            "delta": (child_hits - parent_hits) / total,
            "wins": wins,
            "losses": losses,
            "ties": total - wins - losses,
            "candidate_wilson_95": _wilson(child_hits, total),
            "exact_mcnemar_p": _exact_mcnemar(wins, losses),
        }

    target = metrics("target")
    non_target = metrics("non_target")
    recovered = [
        item for item in pairs["target"].values()
        if not bool(item["parent"].get("oracle_ok"))
        and bool(item["candidate"].get("oracle_ok"))
    ]
    parent_failures = sum(
        not bool(item["parent"].get("oracle_ok"))
        for item in pairs["target"].values()
    )
    recovery_ratio = len(recovered) / parent_failures if parent_failures else 0.0
    covered_designs = sorted({
        str(item["candidate"]["design"]) for item in recovered
    })
    critical_regressions = sum(
        bool(item["parent"].get("oracle_ok"))
        and not bool(item["candidate"].get("oracle_ok"))
        and bool(item["candidate"].get("critical", True))
        for split in pairs.values()
        for item in split.values()
    )
    parent_cost = sum(
        float(item["parent"].get("cost") or 0.0)
        for split in pairs.values() for item in split.values()
    )
    candidate_cost = sum(
        float(item["candidate"].get("cost") or 0.0)
        for split in pairs.values() for item in split.values()
    )
    cost_ratio = (
        candidate_cost / parent_cost
        if parent_cost > 0
        else (1.0 if candidate_cost == 0 else math.inf)
    )
    bound_provenance = dict(provenance or {})
    checks = {
        "target_gain": target["delta"] > 0.0,
        "prior_failure_recovery": len(recovered) >= int(gates["min_prior_failure_recovery"]),
        "target_recovery_ratio": recovery_ratio >= float(gates["min_target_recovery_ratio"]),
        "covered_designs": len(covered_designs) >= int(gates["min_covered_designs"]),
        "non_target_regression": non_target["delta"] >= -float(gates["non_target_epsilon"]),
        "critical_regressions": critical_regressions == 0,
        "candidate_cost": cost_ratio <= float(gates["max_cost_ratio"]),
        "provenance_complete": _provenance_complete(bound_provenance),
    }
    promote = all(checks.values())
    positive = target["delta"] > 0 and non_target["delta"] >= -float(
        gates["non_target_epsilon"]
    )
    decision_label = "strong_promotion" if promote else (
        "provisional_candidate" if positive else "reject"
    )
    result = {
        "schema_version": "r3e-policy-promotion-v2",
        "parent_policy_id": parent.policy_id,
        "parent_policy_hash": parent.policy_hash,
        "candidate_policy_id": candidate.policy_id,
        "candidate_policy_hash": candidate.policy_hash,
        "validation_manifest_hash": validation_manifest_hash,
        "thresholds": gates,
        "target": target,
        "non_target": non_target,
        "prior_failure_recovery": len(recovered),
        "target_recovery_ratio": recovery_ratio,
        "covered_designs": covered_designs,
        "critical_regressions": critical_regressions,
        "parent_cost": parent_cost,
        "candidate_cost": candidate_cost,
        "cost_ratio": cost_ratio,
        "checks": checks,
        "rejection_reasons": [key for key, passed in checks.items() if not passed],
        "decision": decision_label,
        "promote": promote,
        "provenance": bound_provenance,
    }
    result["decision_hash"] = hash_payload(result)
    return result


def build_policy_promotion_bundle(
    parent: PolicyState,
    candidate: PolicyState,
    validation_rows: list[dict[str, Any]],
    *,
    validation_manifest_hash: str,
    target_manifest: dict[str, Any],
    non_target_manifest: dict[str, Any],
    thresholds: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    recorded_decision: dict[str, Any] | None = None,
    memory_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze every input needed for a registry-owned promotion replay.

    A decision hash is only an integrity checksum.  This bundle is the
    authority object: the registry can reconstruct the decision from frozen
    policies, manifests, paired rows, thresholds and provenance without
    trusting any caller-supplied verdict.
    """
    target = verify_manifest(target_manifest)
    non_target = verify_manifest(non_target_manifest)
    bound_provenance = dict(provenance or {})
    if target["manifest_hash"] != bound_provenance.get("target_manifest_hash"):
        raise PromotionViolation("target manifest/provenance hash mismatch")
    if non_target["manifest_hash"] != bound_provenance.get("non_target_manifest_hash"):
        raise PromotionViolation("non-target manifest/provenance hash mismatch")
    if hash_payload(validation_rows) != bound_provenance.get("paired_result_hash"):
        raise PromotionViolation("paired rows/provenance hash mismatch")
    _verify_rows_against_manifests(validation_rows, target, non_target)
    reconstructed = decide_policy_promotion(
        parent,
        candidate,
        validation_rows,
        validation_manifest_hash=validation_manifest_hash,
        thresholds=thresholds,
        provenance=bound_provenance,
    )
    if recorded_decision is not None and recorded_decision != reconstructed:
        raise PromotionViolation("recorded promotion decision is not reconstructable")
    payload = {
        "schema_version": PROMOTION_BUNDLE_SCHEMA_VERSION,
        "parent_policy": parent.to_dict(),
        "candidate_policy": candidate.to_dict(),
        "target_manifest": target,
        "non_target_manifest": non_target,
        "validation_rows": list(validation_rows),
        "validation_manifest_hash": validation_manifest_hash,
        "thresholds": dict(thresholds or {}),
        "provenance": bound_provenance,
        "recorded_decision": reconstructed,
        "memory_authority": dict(memory_authority or {}),
    }
    payload["bundle_hash"] = hash_payload(payload)
    return payload


def verify_policy_promotion_bundle(
    bundle: dict[str, Any],
    *,
    expected_parent: PolicyState | None = None,
    expected_candidate: PolicyState | None = None,
) -> dict[str, Any]:
    """Reconstruct and return the exact authorized promotion decision."""
    required = {
        "schema_version",
        "parent_policy",
        "candidate_policy",
        "target_manifest",
        "non_target_manifest",
        "validation_rows",
        "validation_manifest_hash",
        "thresholds",
        "provenance",
        "recorded_decision",
        "memory_authority",
        "bundle_hash",
    }
    if not isinstance(bundle, dict) or set(bundle) != required:
        raise PromotionViolation("promotion bundle fields mismatch")
    if bundle.get("schema_version") != PROMOTION_BUNDLE_SCHEMA_VERSION:
        raise PromotionViolation("promotion bundle schema mismatch")
    body = {key: value for key, value in bundle.items() if key != "bundle_hash"}
    if bundle.get("bundle_hash") != hash_payload(body):
        raise PromotionViolation("promotion bundle hash mismatch")
    parent = PolicyState.from_dict(bundle["parent_policy"])
    candidate = PolicyState.from_dict(bundle["candidate_policy"])
    if expected_parent is not None and parent.to_dict() != expected_parent.to_dict():
        raise PromotionViolation("promotion bundle parent differs from registry")
    if expected_candidate is not None and candidate.to_dict() != expected_candidate.to_dict():
        raise PromotionViolation("promotion bundle candidate differs from registry")
    target = verify_manifest(bundle["target_manifest"])
    non_target = verify_manifest(bundle["non_target_manifest"])
    provenance = dict(bundle["provenance"])
    if target["manifest_hash"] != provenance.get("target_manifest_hash"):
        raise PromotionViolation("target manifest/provenance hash mismatch")
    if non_target["manifest_hash"] != provenance.get("non_target_manifest_hash"):
        raise PromotionViolation("non-target manifest/provenance hash mismatch")
    rows = bundle["validation_rows"]
    if not isinstance(rows, list):
        raise PromotionViolation("promotion bundle validation_rows must be a list")
    if hash_payload(rows) != provenance.get("paired_result_hash"):
        raise PromotionViolation("paired rows/provenance hash mismatch")
    _verify_rows_against_manifests(rows, target, non_target)
    reconstructed = decide_policy_promotion(
        parent,
        candidate,
        rows,
        validation_manifest_hash=str(bundle["validation_manifest_hash"]),
        thresholds=dict(bundle["thresholds"]),
        provenance=provenance,
    )
    if bundle["recorded_decision"] != reconstructed:
        raise PromotionViolation("recorded promotion decision differs from reconstruction")
    memory_authority = bundle["memory_authority"]
    if candidate.memory_binding:
        if not memory_authority:
            raise PromotionViolation(
                "memory-bound policy requires a reconstructed memory authority bundle"
            )
        # Local import keeps the generic policy layer usable without loading
        # the memory runtime when a policy has no memory binding.
        from r3e.memory.authority import verify_memory_promotion_authority

        verify_memory_promotion_authority(
            memory_authority,
            parent=parent,
            candidate=candidate,
        )
    elif memory_authority:
        raise PromotionViolation("non-memory policy may not carry memory authority")
    return reconstructed


def select_single_promotable_child(decisions: list[dict[str, Any]]) -> dict[str, Any] | None:
    promotable = [item for item in decisions if item.get("promote")]
    if not promotable:
        return None
    return max(
        promotable,
        key=lambda item: (
            float(item["target"]["delta"]),
            int(item["prior_failure_recovery"]),
            -float(item["cost_ratio"]),
            str(item["candidate_policy_id"]),
        ),
    )


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["decide"])
    parser.add_argument("--parent", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--validation-jsonl", required=True)
    parser.add_argument("--validation-manifest-hash", required=True)
    parser.add_argument("--thresholds")
    parser.add_argument("--provenance")
    parser.add_argument("--out")
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in open(args.validation_jsonl, encoding="utf-8")
        if line.strip()
    ]
    decision = decide_policy_promotion(
        PolicyState.from_dict(read_json(args.parent)),
        PolicyState.from_dict(read_json(args.candidate)),
        rows,
        validation_manifest_hash=args.validation_manifest_hash,
        thresholds=read_json(args.thresholds) if args.thresholds else None,
        provenance=read_json(args.provenance) if args.provenance else None,
    )
    if args.out:
        atomic_write_json(args.out, decision)
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
