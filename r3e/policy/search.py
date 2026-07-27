"""Local search over the frozen four-dimensional policy space."""
from __future__ import annotations

import argparse
import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.protocol.hashing import hash_payload, read_json

from .operators import (
    MUTABLE_DIMENSIONS,
    configuration_key,
    one_factor_neighbors,
    residual_conditioned_neighbors,
)
from .schema import PolicyState


class PolicySearchViolation(RuntimeError):
    """Raised when target data leaks into child proposal."""


def _manifest_rows(manifest: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(manifest, list):
        return manifest
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise PolicySearchViolation("adaptation manifest rows missing")
    if manifest.get("split") not in {None, "adaptation"}:
        raise PolicySearchViolation("policy search may only consume adaptation data")
    return rows


def propose_children(
    parent: PolicyState,
    adaptation_manifest: dict[str, Any] | list[dict[str, Any]],
    search_space: dict[str, Any],
    *,
    round_id: str,
    seed: int = 0,
) -> list[PolicyState]:
    if not search_space.get("frozen"):
        raise PolicySearchViolation("policy operator space must be frozen")
    expected_space_hash = hash_payload({
        key: value for key, value in search_space.items() if key != "search_space_hash"
    })
    if search_space.get("search_space_hash") != expected_space_hash:
        raise PolicySearchViolation("policy operator space hash mismatch")
    rows = _manifest_rows(adaptation_manifest)
    residual_hash = (
        adaptation_manifest.get("source_residual_manifest_hash")
        if isinstance(adaptation_manifest, dict)
        else None
    ) or hash_payload(rows)
    mix = search_space.get("operator_mix") or {}
    max_children = int(search_space.get("max_children_per_round") or 6)
    rng = random.Random(seed)
    selected: list[tuple[str, dict[str, Any]]] = []
    seen = {configuration_key(parent.configuration)}

    one_factor = one_factor_neighbors(parent, search_space)
    rng.shuffle(one_factor)
    for proposal in one_factor:
        if len([row for row in selected if row[0].startswith("one_factor:")]) >= int(
            mix.get("one_factor", 3)
        ):
            break
        key = configuration_key(proposal[1])
        if key not in seen:
            selected.append(proposal)
            seen.add(key)

    conditioned = residual_conditioned_neighbors(parent, rows, search_space)
    for proposal in conditioned:
        if len([
            row for row in selected if row[0].startswith("residual_conditioned:")
        ]) >= int(mix.get("residual_conditioned", 2)):
            break
        key = configuration_key(proposal[1])
        if key not in seen:
            selected.append(proposal)
            seen.add(key)

    dimensions = search_space.get("dimensions") or {}
    combinations = [
        (mode, k, n, loop)
        for mode in dimensions.get("evidence_mode", [])
        for k in dimensions.get("evidence_k", [])
        for n in dimensions.get("n_candidates", [])
        for loop in dimensions.get("repair_loop", [])
    ]
    rng.shuffle(combinations)
    for values in combinations:
        if len([row for row in selected if row[0] == "exploration"]) >= int(
            mix.get("exploration", 1)
        ):
            break
        if values in seen:
            continue
        config = deepcopy(parent.configuration)
        for key, value in zip(MUTABLE_DIMENSIONS, values):
            config[key] = value
        selected.append(("exploration", config))
        seen.add(values)

    children = []
    round_number = int("".join(ch for ch in round_id if ch.isdigit()) or parent.created_round + 1)
    for index, (operator, configuration) in enumerate(selected[:max_children], 1):
        child = PolicyState.from_dict({
            "policy_id": f"{parent.policy_id}_{round_id}_C{index:02d}",
            "schema_version": parent.schema_version,
            "parent_policy_id": parent.policy_id,
            "parent_policy_hash": parent.policy_hash,
            "base_policy_hash": parent.base_policy_hash,
            "created_round": round_number,
            "created_from_residual_manifest_hash": residual_hash,
            "configuration": configuration,
            "budgets": deepcopy(parent.budgets),
            "status": "candidate",
            "validation_manifest_hash": "",
            "promotion_decision_hash": "",
            "rollback_policy_id": parent.policy_id,
            "frozen_assets": deepcopy(parent.frozen_assets or {}),
            "proposal_operator": operator,
        })
        children.append(child)
    return children


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-policy", required=True)
    parser.add_argument("--adaptation-manifest", required=True)
    parser.add_argument("--search-space", required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    parent = PolicyState.from_dict(read_json(args.parent_policy))
    children = propose_children(
        parent,
        read_json(args.adaptation_manifest),
        read_json(args.search_space),
        round_id=args.round_id,
        seed=args.seed,
    )
    for child in children:
        print(json.dumps(child.to_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    _main()
