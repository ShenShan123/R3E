"""Local search over the frozen four-dimensional policy space."""
from __future__ import annotations

import argparse
import json
import random
from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.blue.portfolio.schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
)
from r3e.blue.portfolio.allocator import OfflineAllocatorState
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


PORTFOLIO_OPERATOR_SPACE_SCHEMA = "r3e-portfolio-operator-space-v1"


def _verify_portfolio_operator_space(
    operator_space: dict[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(operator_space)
    if set(payload) != {
        "schema_version",
        "frozen",
        "operators",
        "transition_fields",
        "operator_space_hash",
    }:
        raise PolicySearchViolation("portfolio operator space fields mismatch")
    if (
        payload["schema_version"] != PORTFOLIO_OPERATOR_SPACE_SCHEMA
        or payload["frozen"] is not True
        or not isinstance(payload["operators"], list)
        or not payload["operators"]
        or len(payload["operators"]) != len(set(payload["operators"]))
        or not isinstance(payload["transition_fields"], dict)
        or set(payload["transition_fields"]) != set(payload["operators"])
    ):
        raise PolicySearchViolation("portfolio operator space is invalid")
    if payload["operator_space_hash"] != hash_payload({
        key: value
        for key, value in payload.items()
        if key != "operator_space_hash"
    }):
        raise PolicySearchViolation("portfolio operator space hash mismatch")
    component_fields = {
        "router_hash",
        "allocator_hash",
        "selector_hash",
        "semantic_signature_provider_hash",
    }
    for operator, fields in payload["transition_fields"].items():
        if (
            not isinstance(operator, str)
            or not isinstance(fields, list)
            or not fields
            or not set(fields) <= component_fields
            or len(fields) != len(set(fields))
        ):
            raise PolicySearchViolation(
                "portfolio transition field declaration is invalid"
            )
    return payload


def portfolio_conditioned_neighbors(
    parent: PolicyState,
    proposals: list[dict[str, Any]],
    operator_space: dict[str, Any],
    *,
    round_id: str,
    residual_manifest_hash: str,
) -> list[PolicyState]:
    """Create deterministic, one-mechanism portfolio Policy V3 children."""
    space = _verify_portfolio_operator_space(operator_space)
    if parent.schema_version != "r3e-policy-v3":
        raise PolicySearchViolation(
            "portfolio-conditioned search requires Policy V3 parent"
        )
    if not parent.candidate_portfolio_binding:
        raise PolicySearchViolation(
            "portfolio-conditioned parent lacks portfolio binding"
        )
    if not residual_manifest_hash.startswith("sha256:"):
        raise PolicySearchViolation(
            "portfolio search requires residual manifest authority"
        )
    normalized = []
    for proposal in proposals:
        if not isinstance(proposal, dict) or set(proposal) != {
            "operator",
            "portfolio",
        }:
            raise PolicySearchViolation(
                "portfolio proposal fields mismatch"
            )
        operator = str(proposal["operator"])
        if operator not in space["operators"]:
            raise PolicySearchViolation("portfolio proposal operator is frozen")
        portfolio = (
            proposal["portfolio"]
            if isinstance(proposal["portfolio"], CandidatePortfolio)
            else CandidatePortfolio.from_dict(proposal["portfolio"])
        )
        binding = build_candidate_portfolio_binding(portfolio)
        if (
            binding["lens_registry_hash"]
            != parent.candidate_portfolio_binding["lens_registry_hash"]
        ):
            raise PolicySearchViolation(
                "portfolio child cannot replace lens registry"
            )
        changed = {
            field
            for field in (
                "router_hash",
                "allocator_hash",
                "selector_hash",
                "semantic_signature_provider_hash",
            )
            if binding[field]
            != parent.candidate_portfolio_binding[field]
        }
        allowed = set(space["transition_fields"][operator])
        if not changed or changed != allowed:
            raise PolicySearchViolation(
                "portfolio proposal violates one-mechanism transition"
            )
        normalized.append((operator, portfolio, binding))
    normalized.sort(
        key=lambda row: (row[0], row[1].effective_portfolio_hash)
    )
    if len({
        binding["effective_portfolio_hash"]
        for _, _, binding in normalized
    }) != len(normalized):
        raise PolicySearchViolation(
            "portfolio proposals contain duplicate behavior"
        )
    round_number = int(
        "".join(ch for ch in round_id if ch.isdigit())
        or parent.created_round + 1
    )
    children = []
    for index, (operator, _portfolio, binding) in enumerate(
        normalized, 1
    ):
        children.append(PolicyState.from_dict({
            "policy_id": f"{parent.policy_id}_{round_id}_P{index:02d}",
            "schema_version": "r3e-policy-v3",
            "parent_policy_id": parent.policy_id,
            "parent_policy_hash": parent.policy_hash,
            "base_policy_hash": parent.base_policy_hash,
            "created_round": round_number,
            "created_from_residual_manifest_hash": (
                residual_manifest_hash
            ),
            "configuration": deepcopy(parent.configuration),
            "budgets": deepcopy(parent.budgets),
            "status": "candidate",
            "validation_manifest_hash": "",
            "promotion_decision_hash": "",
            "rollback_policy_id": parent.policy_id,
            "frozen_assets": deepcopy(parent.frozen_assets or {}),
            "proposal_operator": f"portfolio:{operator}",
            "memory_binding": {},
            "candidate_portfolio_binding": binding,
        }))
    return children


def propose_offline_allocator_child(
    parent: PolicyState,
    adaptive_portfolio: CandidatePortfolio,
    allocator_state: OfflineAllocatorState,
    operator_space: dict[str, Any],
    *,
    round_id: str,
    residual_manifest_hash: str,
) -> PolicyState:
    """Propose exactly one round-frozen ACP-4 allocator child."""
    if adaptive_portfolio.mode != "adaptive":
        raise PolicySearchViolation(
            "offline allocator child requires adaptive portfolio"
        )
    if (
        adaptive_portfolio.allocator_hash
        != allocator_state.state_hash
        or allocator_state.source_effective_policy_hash
        != parent.effective_policy_hash
        or allocator_state.lens_registry_hash
        != parent.candidate_portfolio_binding.get(
            "lens_registry_hash"
        )
    ):
        raise PolicySearchViolation(
            "offline allocator state is not bound to its parent policy"
        )
    children = portfolio_conditioned_neighbors(
        parent,
        [{
            "operator": "descriptor_routed_to_adaptive",
            "portfolio": adaptive_portfolio,
        }],
        operator_space,
        round_id=round_id,
        residual_manifest_hash=residual_manifest_hash,
    )
    if len(children) != 1:
        raise PolicySearchViolation(
            "offline allocator stage must propose exactly one child"
        )
    return children[0]


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
            # A configuration-changing child must not silently inherit an
            # executable bank. RAAM compatibility/revalidation constructs a
            # separate hash-bound bank candidate when inheritance is proven.
            "memory_binding": {},
            "candidate_portfolio_binding": deepcopy(
                parent.candidate_portfolio_binding or {}
            ),
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
