"""Pre-generation Grounded Red proposal authority.

The runner freezes coverage, curriculum, specialist, archive, and bounded
operator decisions before an adapter is called.  The adapter may choose a
concrete design/node that satisfies an intent, but it cannot change the
family, operator, effect, difficulty target, lineage class, or quota decision.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .coverage import verify_coverage_state
from .registry import GroundedRegistryBundle


PROPOSAL_PLAN_SCHEMA = "r3e-grounded-proposal-authority-v1"
PROPOSAL_INTENT_SCHEMA = "r3e-grounded-proposal-intent-v1"
_ARCHIVE_VIEW_FIELDS = {
    "poison_id",
    "archive_kind",
    "design_id",
    "family_id",
    "affected_role",
    "difficulty_band",
    "authority_hash",
}
_FORBIDDEN_ARCHIVE_FIELDS = {
    "prompt",
    "patch",
    "patch_payload",
    "candidate_patch",
    "reference_patch",
    "reference_repair",
    "source_episode_ids",
    "qualification_evidence",
    "private_raam_evidence",
    "hidden_target",
}


class GroundedProposalViolation(RuntimeError):
    """Raised when pre-generation authority is stale or not reconstructable."""


def grounded_proposal_protocol_hash(
    registries: GroundedRegistryBundle,
) -> str:
    return hash_payload({
        "schema_version": PROPOSAL_PLAN_SCHEMA,
        "intent_schema_version": PROPOSAL_INTENT_SCHEMA,
        "registry_bundle_hash": registries.registry_bundle_hash,
    })


def build_proposal_archive_view(
    *,
    residual_archive: Iterable[Mapping[str, Any]],
    covered_archive: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project runtime archives onto the minimal planner-visible authority."""
    rows = []
    for archive_kind, source in (
        ("residual", residual_archive),
        ("covered", covered_archive),
    ):
        for raw in source:
            if _FORBIDDEN_ARCHIVE_FIELDS & set(raw):
                raise GroundedProposalViolation(
                    "private archive fields reached proposal planning"
                )
            authority = raw.get("grounded_authority_bundle")
            authority_hash = (
                str(authority.get("authority_hash") or "")
                if isinstance(authority, Mapping)
                else ""
            )
            difficulty = raw.get("grounded_difficulty_profile")
            difficulty_band = (
                str(difficulty.get("difficulty_band") or "")
                if isinstance(difficulty, Mapping)
                else str(raw.get("difficulty_band") or "D0")
            )
            row = {
                "poison_id": str(raw.get("poison_id") or ""),
                "archive_kind": archive_kind,
                "design_id": str(
                    raw.get("design")
                    or raw.get("design_id")
                    or raw.get("case_id")
                    or ""
                ),
                "family_id": str(
                    raw.get("family")
                    or (
                        raw.get("grounded_mutation_plan") or {}
                    ).get("family_id")
                    or ""
                ),
                "affected_role": str(
                    raw.get("affected_role") or "unknown"
                ),
                "difficulty_band": difficulty_band or "D0",
                "authority_hash": authority_hash,
            }
            if (
                set(row) != _ARCHIVE_VIEW_FIELDS
                or not row["poison_id"]
                or not row["design_id"]
                or not row["family_id"]
            ):
                continue
            rows.append(row)
    rows.sort(key=lambda row: (
        row["archive_kind"],
        row["family_id"],
        row["design_id"],
        row["poison_id"],
    ))
    if len({row["poison_id"] for row in rows}) != len(rows):
        raise GroundedProposalViolation(
            "proposal archive poison ids must be unique"
        )
    return rows


def _curriculum_band(
    family_id: str,
    coverage_state: Mapping[str, Any],
    *,
    maximum_band: str,
) -> str:
    bands = ["D0", "D1", "D2", "D3"]
    if maximum_band not in bands:
        raise GroundedProposalViolation(
            "proposal maximum difficulty must be D0-D3"
        )
    rows = [
        row for row in coverage_state["cells"]
        if row["family_id"] == family_id
    ]
    if not rows:
        return "D0"
    admitted = sum(int(row["admitted"]) for row in rows)
    covered = sum(int(row["covered"]) for row in rows)
    proposals = sum(int(row["proposals"]) for row in rows)
    rejected = sum(int(row["rejected"]) for row in rows)
    current = max(
        (
            row["difficulty_band"]
            for row in rows
            if row["difficulty_band"] in bands
        ),
        key=bands.index,
        default="D0",
    )
    index = bands.index(current)
    if admitted and covered * 3 >= admitted * 2:
        index += 1
    elif proposals and rejected * 2 >= proposals:
        index -= 1
    return bands[max(0, min(index, bands.index(maximum_band)))]


def _difficulty_target(band: str) -> dict[str, Any]:
    return {
        "difficulty_band": band,
        "dependency_depth_delta": {
            "D0": 0, "D1": 1, "D2": 1, "D3": 2,
        }[band],
        "temporal_depth_delta": {
            "D0": 0, "D1": 0, "D2": 1, "D3": 2,
        }[band],
        "rare_trigger": band in {"D2", "D3"},
        "cross_block": band == "D3",
    }


def _role_for_family(family_id: str) -> str:
    if family_id.startswith(("sequential.", "temporal.")):
        return "state"
    if family_id.startswith(("control.", "protocol.")):
        return "control"
    if family_id.startswith("datapath."):
        return "data"
    return "expression"


def _operator_recipes(
    registries: GroundedRegistryBundle,
) -> list[tuple[str, str, str]]:
    recipes = []
    for operator_id, operator in sorted(registries.operators.items()):
        for family_id in sorted(operator["supported_family_ids"]):
            effects = sorted(
                effect_id
                for effect_id, effect in registries.effects.items()
                if family_id in effect["supported_family_ids"]
            )
            if effects:
                recipes.append((family_id, operator_id, effects[0]))
    if not recipes:
        raise GroundedProposalViolation(
            "Grounded registries have no executable recipes"
        )
    return recipes


def _intent(
    *,
    policy: PolicyState,
    index: int,
    family_id: str,
    operator_id: str,
    effect_id: str,
    specialist_kind: str,
    dispatch_kind: str,
    difficulty_target: Mapping[str, Any],
    parent_poison_ids: Iterable[str],
    target_memory_ids: Iterable[str],
    memory_operator: str,
    saturation: int,
    archive_support: int,
) -> dict[str, Any]:
    parents = sorted(set(str(value) for value in parent_poison_ids))
    memories = sorted(set(str(value) for value in target_memory_ids))
    body = {
        "schema_version": PROPOSAL_INTENT_SCHEMA,
        "intent_id": f"GPI-{index:04d}",
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": (
            policy.effective_policy_hash
        ),
        "family_id": family_id,
        "operator_id": operator_id,
        "expected_runtime_effect_id": effect_id,
        "specialist_kind": specialist_kind,
        "dispatch_kind": dispatch_kind,
        "target_role": _role_for_family(family_id),
        "temporal_context": (
            "sequential"
            if family_id.startswith(("sequential.", "temporal."))
            else "combinational"
        ),
        "difficulty_target": deepcopy(dict(difficulty_target)),
        "parent_poison_ids": parents,
        "target_memory_ids": memories,
        "memory_operator": memory_operator,
        "pareto_vector": {
            "family_saturation": int(saturation),
            "archive_support": int(archive_support),
            "difficulty_rank": ["D0", "D1", "D2", "D3", "D4"].index(
                str(difficulty_target["difficulty_band"])
            ),
        },
    }
    body["intent_hash"] = hash_payload(body)
    return body


def _construct_plan(
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    coverage_state: Mapping[str, Any],
    archive_view: Iterable[Mapping[str, Any]],
    budget: int,
    family_quota: int,
    archive_quota: int,
    maximum_difficulty_band: str,
    memory_capability: Mapping[str, Any] | None,
    allow_composition: bool,
) -> dict[str, Any]:
    state = verify_coverage_state(coverage_state)
    archive = [deepcopy(dict(row)) for row in archive_view]
    if any(set(row) != _ARCHIVE_VIEW_FIELDS for row in archive):
        raise GroundedProposalViolation(
            "proposal archive view fields mismatch"
        )
    if budget < 1 or family_quota < 1 or archive_quota < 0:
        raise GroundedProposalViolation(
            "proposal budget and quotas are invalid"
        )
    family_counts = {
        family_id: sum(
            int(row["admitted"])
            for row in state["cells"]
            if row["family_id"] == family_id
        )
        for family_id in registries.families
    }
    archive_by_family = {
        family_id: [
            row for row in archive if row["family_id"] == family_id
        ]
        for family_id in registries.families
    }
    raw = []
    for family_id, operator_id, effect_id in _operator_recipes(
        registries
    ):
        band = _curriculum_band(
            family_id,
            state,
            maximum_band=maximum_difficulty_band,
        )
        parents = archive_by_family.get(family_id) or []
        raw.append({
            "family_id": family_id,
            "operator_id": operator_id,
            "effect_id": effect_id,
            "specialist_kind": (
                "hardness_escalator" if parents else "coverage_explorer"
            ),
            "dispatch_kind": "single_ast",
            "difficulty_target": _difficulty_target(band),
            "parent_poison_ids": (
                [parents[0]["poison_id"]] if parents else []
            ),
            "target_memory_ids": [],
            "memory_operator": "",
            "saturation": family_counts.get(family_id, 0),
            "archive_support": len(parents),
        })
    memory_hash = ""
    if memory_capability is not None:
        memory_hash = str(memory_capability.get("packet_hash") or "")
        summaries = list(
            memory_capability.get("active_memory_summaries") or []
        )
        allowed = sorted(
            memory_capability.get("allowed_operators") or []
        )
        residuals = [
            row for row in archive if row["archive_kind"] == "residual"
        ]
        if summaries and allowed and residuals:
            recipe = _operator_recipes(registries)[0]
            operator = (
                "memory_conflict"
                if len(summaries) >= 2
                and "memory_conflict" in allowed
                else allowed[0]
            )
            target_count = 2 if operator == "memory_conflict" else 1
            raw.append({
                "family_id": recipe[0],
                "operator_id": recipe[1],
                "effect_id": recipe[2],
                "specialist_kind": "memory_adversary",
                "dispatch_kind": "parser_memory",
                "difficulty_target": _difficulty_target("D2"),
                "parent_poison_ids": [residuals[0]["poison_id"]],
                "target_memory_ids": [
                    str(row["memory_id"])
                    for row in summaries[:target_count]
                ],
                "memory_operator": operator,
                "saturation": family_counts.get(recipe[0], 0),
                "archive_support": len(residuals),
            })
    if allow_composition:
        admissible = [
            row for row in archive
            if row["archive_kind"] == "residual"
            and row["authority_hash"]
        ]
        pairs = [
            (left, right)
            for index, left in enumerate(admissible)
            for right in admissible[index + 1:]
            if left["design_id"] == right["design_id"]
            and left["family_id"] != right["family_id"]
        ]
        if pairs:
            left, right = pairs[0]
            raw.append({
                "family_id": "composition.controlled_pair",
                "operator_id": "sequential_ast_composition",
                "effect_id": "composed_functional_failure",
                "specialist_kind": "composition_specialist",
                "dispatch_kind": "controlled_composition",
                "difficulty_target": {
                    **_difficulty_target("D3"),
                    "difficulty_band": "D4",
                },
                "parent_poison_ids": [
                    left["poison_id"], right["poison_id"],
                ],
                "target_memory_ids": [],
                "memory_operator": "",
                "saturation": 0,
                "archive_support": 2,
            })
    raw.sort(key=lambda row: (
        row["saturation"],
        -row["archive_support"],
        -["D0", "D1", "D2", "D3", "D4"].index(
            row["difficulty_target"]["difficulty_band"]
        ),
        row["family_id"],
        row["operator_id"],
    ))
    intents = [
        _intent(policy=policy, index=index, **row)
        for index, row in enumerate(raw, 1)
    ]
    selected = []
    family_selected: dict[str, int] = {}
    archive_selected = 0
    for intent in intents:
        family = intent["family_id"]
        if family_selected.get(family, 0) >= family_quota:
            continue
        if intent["parent_poison_ids"]:
            if archive_selected >= archive_quota:
                continue
            archive_selected += 1
        selected.append(intent["intent_id"])
        family_selected[family] = family_selected.get(family, 0) + 1
        if len(selected) == budget:
            break
    body = {
        "schema_version": PROPOSAL_PLAN_SCHEMA,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "registry_bundle_hash": registries.registry_bundle_hash,
        "coverage_state_hash_before": state["coverage_hash"],
        "archive_view_hash": hash_payload(archive),
        "memory_capability_packet_hash": memory_hash,
        "budget": int(budget),
        "family_quota": int(family_quota),
        "archive_quota": int(archive_quota),
        "maximum_difficulty_band": maximum_difficulty_band,
        "allow_composition": bool(allow_composition),
        "candidate_intents": intents,
        "selected_intent_ids": selected,
        "deferred_intent_ids": sorted(
            set(intent["intent_id"] for intent in intents) - set(selected)
        ),
    }
    body["plan_hash"] = hash_payload(body)
    return body


def build_grounded_proposal_plan(
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    coverage_state: Mapping[str, Any],
    archive_view: Iterable[Mapping[str, Any]],
    budget: int,
    family_quota: int,
    archive_quota: int,
    maximum_difficulty_band: str = "D3",
    memory_capability: Mapping[str, Any] | None = None,
    allow_composition: bool = True,
) -> dict[str, Any]:
    archive = [deepcopy(dict(row)) for row in archive_view]
    plan = _construct_plan(
        policy=policy,
        registries=registries,
        coverage_state=coverage_state,
        archive_view=archive,
        budget=budget,
        family_quota=family_quota,
        archive_quota=archive_quota,
        maximum_difficulty_band=maximum_difficulty_band,
        memory_capability=memory_capability,
        allow_composition=allow_composition,
    )
    return verify_grounded_proposal_plan(
        plan,
        policy=policy,
        registries=registries,
        coverage_state=coverage_state,
        archive_view=archive,
        memory_capability=memory_capability,
    )


def verify_grounded_proposal_plan(
    plan: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    coverage_state: Mapping[str, Any],
    archive_view: Iterable[Mapping[str, Any]],
    memory_capability: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = deepcopy(dict(plan))
    if (
        payload.get("schema_version") != PROPOSAL_PLAN_SCHEMA
        or payload.get("plan_hash") != hash_payload({
            key: value for key, value in payload.items()
            if key != "plan_hash"
        })
        or payload.get("challenged_policy_hash") != policy.policy_hash
        or payload.get("challenged_effective_policy_hash")
        != policy.effective_policy_hash
        or payload.get("registry_bundle_hash")
        != registries.registry_bundle_hash
    ):
        raise GroundedProposalViolation(
            "proposal plan envelope or authority mismatch"
        )
    rebuilt = _construct_plan(
        policy=policy,
        registries=registries,
        coverage_state=coverage_state,
        archive_view=archive_view,
        budget=int(payload["budget"]),
        family_quota=int(payload["family_quota"]),
        archive_quota=int(payload["archive_quota"]),
        maximum_difficulty_band=str(
            payload["maximum_difficulty_band"]
        ),
        memory_capability=memory_capability,
        allow_composition=bool(payload["allow_composition"]),
    )
    if payload != rebuilt:
        raise GroundedProposalViolation(
            "proposal plan cannot be deterministically reconstructed"
        )
    return payload


def verify_proposal_candidates(
    plan: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
    *,
    policy: PolicyState,
) -> dict[str, Any]:
    selected = {
        intent["intent_id"]: intent
        for intent in plan["candidate_intents"]
        if intent["intent_id"] in plan["selected_intent_ids"]
    }
    rows = [deepcopy(dict(row)) for row in candidates]
    by_intent = {
        str(row.get("grounded_proposal_intent_id") or ""): row
        for row in rows
    }
    if set(by_intent) != set(selected) or len(by_intent) != len(rows):
        raise GroundedProposalViolation(
            "generated candidates do not cover selected proposal intents"
        )
    bindings = []
    for intent_id in plan["selected_intent_ids"]:
        intent = selected[intent_id]
        row = by_intent[intent_id]
        if (
            row.get("grounded_proposal_intent_hash")
            != intent["intent_hash"]
            or row.get("challenged_policy_hash") != policy.policy_hash
            or row.get("family") != intent["family_id"]
            or row.get("effect")
            != intent["expected_runtime_effect_id"]
            or row.get("grounded_dispatch_kind")
            != intent["dispatch_kind"]
            or row.get("grounded_difficulty_target")
            != intent["difficulty_target"]
            or row.get("grounded_proposal_parent_poison_ids")
            != intent["parent_poison_ids"]
            or row.get("grounded_proposal_target_memory_ids")
            != intent["target_memory_ids"]
            or row.get("grounded_proposal_memory_operator")
            != intent["memory_operator"]
        ):
            raise GroundedProposalViolation(
                "generated candidate widens proposal intent authority"
            )
        bindings.append({
            "intent_id": intent_id,
            "intent_hash": intent["intent_hash"],
            "poison_id": str(row.get("poison_id") or ""),
        })
    authority = {
        "schema_version": "r3e-grounded-proposal-execution-v1",
        "proposal_plan_hash": plan["plan_hash"],
        "challenged_policy_hash": policy.policy_hash,
        "candidate_bindings": bindings,
    }
    authority["execution_hash"] = hash_payload(authority)
    return authority
