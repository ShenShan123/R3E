"""Author knowledge items from verified repair episodes.

``DeterministicKnowledgeAuthor`` is used while LLM calls are disabled.

Experience-derived parts:
- the grouping;
- the applicability conditions (only features shared by every member);
- the causal chain;
- the preservation constraints;
- the abstained or failed edit classes;
- the anonymized example.

The per-bug-type text in ``TYPE_GUIDANCE`` is a fixed, generic vocabulary. It
supplies wording, not decisions: which entry is used, and with which facts, is
determined by the verified episodes.

``LlmKnowledgeAuthor`` defines the interface for a model-written card. It
builds requests from the same episode content, and parses and validates the
responses. It makes no calls unless a client is supplied.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable, Mapping, Protocol, Sequence

from r3e.protocol.hashing import hash_payload

from .schema import (
    CARD_FIELDS,
    KnowledgeItem,
    RepairEpisode,
    reject_forbidden,
)


TYPE_GUIDANCE: Mapping[str, Mapping[str, Any]] = {
    "operator_compare": {
        "hypothesis": "a comparison admits or excludes one boundary value",
        "checks": ["List every comparison feeding the failing output and check its boundary (< vs <=, > vs >=, == vs !=)."],
        "principles": ["Change the comparison operator before changing constants on either side."],
    },
    "operator_arith": {
        "hypothesis": "an arithmetic operator computes the wrong update",
        "checks": ["Compare the per-cycle step of the expected and observed values to identify the faulty arithmetic."],
        "principles": ["Correct the operator in the update expression; keep operand widths unchanged."],
    },
    "operator_logic": {
        "hypothesis": "a logical or bitwise operator combines its inputs incorrectly",
        "checks": ["Evaluate the logic expression feeding the failing output for the first failing input vector."],
        "principles": ["Swap or negate the single operator that explains the first wrong bit."],
    },
    "operator_shift": {
        "hypothesis": "a shift direction or amount is wrong",
        "checks": ["Check whether observed values are scaled by a power of two relative to expected."],
        "principles": ["Correct the shift direction or amount; do not add masking logic."],
    },
    "constant_value": {
        "hypothesis": "a literal constant (limit, increment, initial or reset value) is off",
        "checks": ["Locate literals in the cone of the failing output and test which one explains the observed offset."],
        "principles": ["Adjust the single literal whose change matches the observed delta."],
    },
    "width_or_index": {
        "hypothesis": "a declared width, slice or index range is wrong",
        "checks": ["Compare declared widths and slice bounds along the path to the failing output."],
        "principles": ["Fix the range bound; avoid adding extension or truncation logic elsewhere."],
    },
    "condition_expression": {
        "hypothesis": "a branch or case condition selects the wrong path",
        "checks": ["At the first failing cycle, evaluate each guarding condition and identify the branch that should have been taken."],
        "principles": ["Correct the guarding condition rather than the assignments inside the branch."],
    },
    "assignment_kind": {
        "hypothesis": "blocking vs nonblocking assignment changes when a value becomes visible",
        "checks": ["Check whether the failing value appears one cycle early or late relative to its sources."],
        "principles": ["Use nonblocking assignments for clocked registers and blocking assignments for combinational logic."],
    },
    "assignment_target": {
        "hypothesis": "a value is written to the wrong destination",
        "checks": ["Check which signals are assigned in the block driving the failing output."],
        "principles": ["Redirect the assignment to the intended destination; do not duplicate writes."],
    },
    "signal_reference": {
        "hypothesis": "an expression reads the wrong source signal",
        "checks": ["For the failing output's driver, check whether each source operand has the expected timing and meaning."],
        "principles": ["Replace the single wrong operand; keep the expression shape."],
    },
    "state_transition": {
        "hypothesis": "a state register or its next-state logic takes the wrong transition",
        "checks": ["Trace the state sequence up to the first failing cycle and find the first wrong transition."],
        "principles": ["Correct the next-state assignment for the wrong transition only."],
    },
    "reset_or_enable": {
        "hypothesis": "reset or enable gating is inverted, missing or applied to the wrong branch",
        "checks": ["Check the reset/enable condition polarity and which branch it guards."],
        "principles": ["Fix the polarity or guard of the reset/enable condition; keep reset values unchanged unless implicated."],
    },
    "sensitivity_list": {
        "hypothesis": "the sensitivity list omits or adds an event",
        "checks": ["Check whether the block reacts to every signal or edge it depends on."],
        "principles": ["Correct the event list; prefer @(*) for combinational blocks."],
    },
    "missing_or_extra_logic": {
        "hypothesis": "a statement is missing or superfluous",
        "checks": ["Check for a missing default/else assignment or an extra update in the failing path."],
        "principles": ["Add or remove one statement; avoid restructuring the block."],
    },
    "expression_rewrite": {
        "hypothesis": "an expression feeding the failing output is formulated incorrectly",
        "checks": ["Recompute the expression by hand for the first failing cycle."],
        "principles": ["Rewrite only the expression that explains the first wrong value."],
    },
}
SYMPTOM_TEXT = {
    "constant_offset": "observed value differs from expected by a constant offset",
    "step_rate_mismatch": "observed value changes at a different per-cycle rate",
    "observed_leads_one_cycle": "observed value appears one cycle early",
    "observed_lags_one_cycle": "observed value appears one cycle late",
    "stuck_value": "observed value stops changing while expected keeps changing",
    "single_cycle_glitch": "observed value is wrong for a single cycle",
    "x_or_unknown": "observed value is X/unknown",
    "variable_offset": "observed value differs by a varying amount",
}
REGISTER_TEXT = {
    "0": "the output is itself a register",
    "1": "the output is one hop from a register",
    "2+": "the nearest register is two or more hops away",
    "combinational_only": "no register is in the output's cone",
}


def _consensus(profiles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    first = dict(profiles[0])
    return {
        key: value for key, value in first.items()
        if all(p.get(key) == value for p in profiles[1:])
    }


class KnowledgeAuthor(Protocol):
    author_id: str
    author_version: str

    def author(self, episodes: Iterable[RepairEpisode]) -> list[KnowledgeItem]: ...


class DeterministicKnowledgeAuthor:
    author_id = "deterministic-knowledge-author"
    author_version = "1"

    @staticmethod
    def group_key(episode: RepairEpisode) -> tuple[str, str, str, str]:
        profile = episode.profile
        return (
            str(episode.verified_bug_type),
            str(profile.status.get("symptom", "unknown")),
            str(profile.causal.get("output_driver_kind", "unknown")),
            str(profile.causal.get("register_distance", "unknown")),
        )

    def author(self, episodes: Iterable[RepairEpisode]) -> list[KnowledgeItem]:
        groups: dict[tuple[str, str, str, str], list[RepairEpisode]] = defaultdict(list)
        failed_edits: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for episode in episodes:
            profile = episode.profile
            short = (
                str(profile.status.get("symptom", "unknown")),
                str(profile.causal.get("output_driver_kind", "unknown")),
                str(profile.causal.get("register_distance", "unknown")),
            )
            for attempt in episode.get("attempts") or []:
                if attempt.get("verdict_tier") in {"compile_fail", "visible_fail"} and attempt.get("edit_class"):
                    failed_edits[short].add(str(attempt["edit_class"]))
            if episode.verified_bug_type is None:
                continue
            groups[self.group_key(episode)].append(episode)
        items = []
        for key, members in sorted(groups.items()):
            members.sort(key=lambda e: e.episode_hash)
            items.append(self._item(key, members, failed_edits.get(key[1:], set())))
        return items

    def _item(
        self,
        key: tuple[str, str, str, str],
        members: list[RepairEpisode],
        failed_edit_classes: set[str],
    ) -> KnowledgeItem:
        bug_type = key[0]
        status = _consensus([m.profile.status for m in members])
        causal = _consensus([m.profile.causal for m in members])
        classifications = [m["verified_repair"]["classification"] for m in members]
        guidance = TYPE_GUIDANCE[bug_type]
        symptom = status.get("symptom", key[1])
        scopes = sorted({c.get("edit_scope", "unknown") for c in classifications})
        block_kinds = sorted({c.get("block_kind", "unknown") for c in classifications})
        roles = sorted({c.get("assigned_signal_role", "unknown") for c in classifications})
        in_cone = [c.get("edit_in_failing_cone") for c in classifications]
        designs = sorted({str(m["design_cluster"]) for m in members})

        chain = [f"Symptom: {SYMPTOM_TEXT.get(symptom, symptom)}"
                 + (f", first seen at onset bucket '{status['onset_bucket']}'" if "onset_bucket" in status else "")
                 + "."]
        if "output_driver_kind" in causal:
            chain.append(f"The failing output is driven by {causal['output_driver_kind']} logic"
                         + (f"; {REGISTER_TEXT.get(str(causal.get('register_distance')), '')}"
                            if causal.get("register_distance") in REGISTER_TEXT else "") + ".")
        cone_bits = [name for flag, name in (
            ("cone_has_counter", "a counter"),
            ("cone_has_state_machine", "a state machine"),
            ("cone_has_reset_logic", "reset logic"),
        ) if causal.get(flag) is True]
        if cone_bits:
            chain.append("Its cone contains " + ", ".join(cone_bits) + ".")
        chain.append(
            f"In verified repairs the faulty construct was in a {'/'.join(block_kinds)} block, "
            f"assigning a signal of role {'/'.join(roles)}"
            + ("; it lay inside the failing output's cone" if in_cone and all(x is True for x in in_cone) else "")
            + "."
        )

        diagnostics = [
            "Start from the first failing output at its first divergent cycle and compare the last correct value with the first wrong value.",
        ]
        if causal.get("register_distance") in {"1", "2+"}:
            diagnostics.append("Follow the output's cone back to the nearest register before editing.")
        diagnostics.extend(guidance["checks"])

        principles = list(guidance["principles"])
        principles.append(f"Verified repairs were {'/'.join(scopes)} edits; prefer an edit of that size.")
        if failed_edit_classes:
            principles.append(
                "Under the same symptom, attempts of these kinds failed: "
                + ", ".join(sorted(failed_edit_classes)) + "."
            )

        preservation = [
            "Do not change module ports, declared widths of passing outputs, or the clocking scheme.",
            "Keep reset values and untouched assignments exactly as they are.",
        ]
        if status.get("partial_failure") is True:
            preservation.insert(0, "Outputs that already pass the visible test must remain unchanged.")

        limits = [
            f"Derived from {len(members)} verified repair(s) on {len(designs)} design cluster(s); "
            "treat as a hypothesis to check against current evidence.",
        ]
        if len(members) == 1:
            limits.append("Single source: conditions may be narrower than stated.")

        example_src = min(
            members,
            key=lambda m: m["verified_repair"]["classification"].get("changed_token_count", 10**6),
        )["verified_repair"]["anonymized_example"]
        applicability = {"bug_type": bug_type, "status": status, "causal": causal}
        item_id = "K_" + hash_payload(applicability).split(":", 1)[1][:16]
        return KnowledgeItem.create(
            item_id=item_id,
            version=1,
            applicability=applicability,
            card={
                "mechanism_hypothesis": (
                    f"When {SYMPTOM_TEXT.get(symptom, symptom)}, a likely cause is that "
                    f"{guidance['hypothesis']}."
                ),
                "causal_chain": chain,
                "diagnostic_steps": diagnostics,
                "repair_principles": principles,
                "preservation_constraints": preservation,
                "scope_and_limits": limits,
            },
            example={
                "before": list(example_src["before"]),
                "after": list(example_src["after"]),
            } if example_src.get("before") or example_src.get("after") else None,
            evidence={
                "source_episode_hashes": [m.episode_hash for m in members],
                "source_design_clusters": designs,
                "support": len(members),
                "verdict_tiers": sorted({m["verified_repair"]["verdict_tier"] for m in members}),
            },
            author={"author_id": self.author_id, "author_version": self.author_version},
        )


class LlmKnowledgeAuthor:
    """Model-written knowledge cards, validated against the same schema.

    No call is made unless ``client`` is provided. The client must expose
    ``complete_json(messages=..., seed=...) -> {"result": dict, ...}`` (the
    interface of the existing OpenAI-compatible clients).
    """

    author_id = "llm-knowledge-author"
    author_version = "1"

    def __init__(self, client: Any | None = None, *, seed: int = 0):
        self.client = client
        self.seed = int(seed)
        self._fallback = DeterministicKnowledgeAuthor()

    def build_request(self, members: Sequence[RepairEpisode]) -> list[dict[str, str]]:
        evidence = []
        for member in members:
            body = member.to_dict()
            reject_forbidden(body, where="llm_author.request")
            evidence.append({
                "profile": body["profile"],
                "classification": body["verified_repair"]["classification"],
                "example": body["verified_repair"]["anonymized_example"],
                "failed_attempt_classes": sorted({
                    str(a.get("edit_class")) for a in body["attempts"]
                    if a.get("verdict_tier") in {"compile_fail", "visible_fail"} and a.get("edit_class")
                }),
            })
        system = (
            "You write one reusable RTL repair knowledge card from verified repairs. "
            "State the failure mechanism as a hypothesis, not a fact. Do not name design "
            "signals or modules. Return strict JSON with exactly these fields: "
            + ", ".join(CARD_FIELDS)
            + ". mechanism_hypothesis is a string; every other field is a list of strings."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"verified_repairs": evidence}, sort_keys=True)},
        ]

    def parse_response(
        self, result: Mapping[str, Any], members: Sequence[RepairEpisode]
    ) -> KnowledgeItem:
        base = self._fallback._item(
            self._fallback.group_key(members[0]), list(members), set()
        ).to_dict()
        card = {key: result.get(key) for key in CARD_FIELDS}
        return KnowledgeItem.create(
            item_id=base["item_id"],
            version=base["version"],
            applicability=base["applicability"],
            card=card,
            example=base["example"],
            evidence=base["evidence"],
            author={"author_id": self.author_id, "author_version": self.author_version},
        )

    def author(self, episodes: Iterable[RepairEpisode]) -> list[KnowledgeItem]:
        if self.client is None:
            raise RuntimeError("LLM knowledge authoring is disabled: no client supplied")
        groups: dict[tuple[str, str, str, str], list[RepairEpisode]] = defaultdict(list)
        for episode in episodes:
            if episode.verified_bug_type is not None:
                groups[self._fallback.group_key(episode)].append(episode)
        items = []
        for _, members in sorted(groups.items()):
            response = self.client.complete_json(
                messages=self.build_request(members), seed=self.seed
            )
            items.append(self.parse_response(response["result"], members))
        return items
