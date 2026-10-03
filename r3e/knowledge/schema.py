"""Schemas for experience-derived repair knowledge.

RAAM ``ControlMemory`` (``r3e/memory``) may only carry a whitelisted control
delta. A knowledge item in this package instead carries *content* for the Blue
agent:
- a mechanism hypothesis;
- a causal chain;
- diagnostic steps;
- repair principles;
- preservation constraints;
- an anonymized example.

That content is derived from two sources only: the visible test feedback of
past encounters, and Blue's own verified repairs.

Information boundary, enforced here:
- Golden RTL, reference patches, hidden tests and Red's mutation labels never
  enter an episode, a profile or a knowledge item.
- Applicability conditions use normalized features only. Design-specific
  identifiers such as signal names never appear in them.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


KNOWLEDGE_SCHEMA_VERSION = "r3e-repair-knowledge-v2-cases"
EPISODE_SCHEMA_VERSION = "r3e-repair-episode-v2"
FEEDBACK_SCHEMA_VERSION = "r3e-visible-feedback-v1"
PROFILE_SCHEMA_VERSION = "r3e-observable-profile-v1"
BUNDLE_SCHEMA_VERSION = "r3e-knowledge-bundle-v1"

FORBIDDEN_KEYS = frozenset({
    # golden / reference artefacts
    "golden_rtl", "golden_source", "golden_patch", "reference_rtl",
    "reference_source", "reference_patch", "reference_patch_hash",
    "ground_truth_patch",
    # hidden evaluation artefacts
    "hidden_tests", "hidden_testbench", "hidden_verdict",
    # Red-side truth
    "mutation_type", "mutation_family", "mutation_operator", "mutator_rationale",
    "red_truth", "red_label", "red_family", "red_effect", "poison_family",
    "bug_family", "effect_id",
})

BUG_TYPES = (
    "operator_compare",
    "operator_arith",
    "operator_logic",
    "operator_shift",
    "constant_value",
    "width_or_index",
    "condition_expression",
    "assignment_kind",
    "assignment_target",
    "signal_reference",
    "state_transition",
    "reset_or_enable",
    "sensitivity_list",
    "missing_or_extra_logic",
    "expression_rewrite",
)
STATUS_FIELDS = (
    "oracle_stage",
    "symptom",
    "delta_bucket",
    "onset_bucket",
    "failing_output_bucket",
    "partial_failure",
    "x_involved",
    "output_width_bucket",
)
CAUSAL_FIELDS = (
    "output_driver_kind",
    "register_distance",
    "cone_size_bucket",
    "temporal_relation",
    "cone_has_counter",
    "cone_has_state_machine",
    "cone_has_reset_logic",
    "cone_has_control_dependency",
)
ORDINAL_BUCKETS = {
    "onset_bucket": ("cycle0", "early", "mid", "late"),
    "failing_output_bucket": ("1", "2-3", "4+"),
    "output_width_bucket": ("1bit", "narrow", "wide"),
    "register_distance": ("0", "1", "2+"),
    "cone_size_bucket": ("small", "medium", "large"),
    "delta_bucket": ("large_neg", "small_neg", "-1", "+1", "small_pos", "large_pos"),
}
KNOWLEDGE_STATUSES = (
    "candidate", "qualified", "active", "rejected", "suspended", "retired",
)
VERDICT_TIERS = (
    "no_answer",       # the model exhausted its own output budget without an answer
    "compile_fail",
    "visible_fail",
    "visible_pass",
    "hidden_pass",
    "formal_pass",
)
VERIFIED_REPAIR_TIERS = frozenset({"visible_pass", "hidden_pass", "formal_pass"})


class KnowledgeValidationError(ValueError):
    """Raised when knowledge content crosses the information boundary."""


def reject_forbidden(value: Any, *, where: str) -> None:
    """Recursively reject forbidden keys anywhere inside ``value``."""
    if isinstance(value, Mapping):
        leaked = FORBIDDEN_KEYS & {str(key) for key in value}
        if leaked:
            raise KnowledgeValidationError(
                f"{where} contains forbidden fields: {sorted(leaked)}"
            )
        for key, item in value.items():
            reject_forbidden(item, where=f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            reject_forbidden(item, where=f"{where}[{index}]")


@dataclass(frozen=True)
class SignalDivergence:
    """First divergence of one observable output in the visible test."""

    signal: str
    width: int
    first_cycle: int
    expected: str
    observed: str
    symptom: str
    delta: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "width": self.width,
            "first_cycle": self.first_cycle,
            "expected": self.expected,
            "observed": self.observed,
            "symptom": self.symptom,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class VisibleFeedback:
    """What the visible test told Blue about the current candidate or bug.

    ``divergences`` may name design signals. That is acceptable, because this
    is evidence about the current case only. Profiles and knowledge items never
    copy those names.
    """

    oracle_stage: str
    compile_ok: bool
    divergences: tuple[SignalDivergence, ...] = ()
    passing_outputs: tuple[str, ...] = ()
    total_cycles: int = 0
    compile_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "oracle_stage": self.oracle_stage,
            "compile_ok": self.compile_ok,
            "divergences": [item.to_dict() for item in self.divergences],
            "passing_outputs": list(self.passing_outputs),
            "total_cycles": self.total_cycles,
            "compile_message": self.compile_message,
        }

    @property
    def feedback_hash(self) -> str:
        return hash_payload(self.to_dict())

    @property
    def primary(self) -> SignalDivergence | None:
        return self.divergences[0] if self.divergences else None


@dataclass(frozen=True)
class ObservableProfile:
    """Normalized, design-independent features of a failure."""

    status: Mapping[str, Any]
    causal: Mapping[str, Any]

    def __post_init__(self) -> None:
        unknown = set(self.status) - set(STATUS_FIELDS)
        if unknown:
            raise KnowledgeValidationError(f"unknown status fields: {sorted(unknown)}")
        unknown = set(self.causal) - set(CAUSAL_FIELDS)
        if unknown:
            raise KnowledgeValidationError(f"unknown causal fields: {sorted(unknown)}")
        for group in (self.status, self.causal):
            for key, value in group.items():
                if not isinstance(value, (str, bool)):
                    raise KnowledgeValidationError(
                        f"profile field {key} must be a normalized str/bool"
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "status": dict(sorted(self.status.items())),
            "causal": dict(sorted(self.causal.items())),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ObservableProfile":
        return cls(
            status=dict(payload.get("status") or {}),
            causal=dict(payload.get("causal") or {}),
        )

    @property
    def profile_hash(self) -> str:
        return hash_payload(self.to_dict())

    def flat(self) -> dict[str, Any]:
        out = {f"status.{k}": v for k, v in self.status.items()}
        out.update({f"causal.{k}": v for k, v in self.causal.items()})
        return out


def _hashed(payload: dict[str, Any], hash_field: str) -> dict[str, Any]:
    body = {key: value for key, value in payload.items() if key != hash_field}
    payload[hash_field] = hash_payload(body)
    return payload


@dataclass(frozen=True)
class RepairEpisode:
    """One Blue encounter, with everything later knowledge may be derived from.

    ``verified_repair`` describes Blue's *own* candidate that passed the
    declared verdict tier. It is never a golden or reference patch.
    """

    payload: Mapping[str, Any] = field(default_factory=dict)

    REQUIRED = (
        "episode_id", "case_id", "design_cluster", "buggy_rtl_hash",
        "feedback", "profile", "attempts", "verified_repair", "outcome",
        "provenance",
    )

    @classmethod
    def create(cls, **fields: Any) -> "RepairEpisode":
        missing = [key for key in cls.REQUIRED if key not in fields]
        if missing:
            raise KnowledgeValidationError(f"episode missing fields: {missing}")
        reject_forbidden(fields, where="episode")
        if fields["outcome"] not in {"resolved", "unresolved", "inconclusive"}:
            raise KnowledgeValidationError("episode outcome is invalid")
        repair = fields["verified_repair"]
        if repair is not None:
            tier = repair.get("verdict_tier")
            if tier not in VERIFIED_REPAIR_TIERS:
                raise KnowledgeValidationError(
                    "verified_repair requires a passing verdict tier"
                )
            if repair.get("source") != "blue_candidate":
                raise KnowledgeValidationError(
                    "verified_repair must come from Blue's own candidate"
                )
        for attempt in fields["attempts"]:
            if attempt.get("verdict_tier") not in VERDICT_TIERS:
                raise KnowledgeValidationError("attempt verdict tier is invalid")
        ObservableProfile.from_dict(fields["profile"])
        payload = {"schema_version": EPISODE_SCHEMA_VERSION, **deepcopy(fields)}
        return cls(_hashed(payload, "episode_hash"))

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    @property
    def episode_hash(self) -> str:
        return str(self.payload["episode_hash"])

    @property
    def profile(self) -> ObservableProfile:
        return ObservableProfile.from_dict(self.payload["profile"])

    @property
    def verified_bug_type(self) -> str | None:
        repair = self.payload.get("verified_repair")
        if not repair:
            return None
        return (repair.get("classification") or {}).get("bug_type")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self.payload))


CARD_FIELDS = ("cases",)
# Each case is one verified repair's causal chain (see cases.py); no authored
# rules, hypotheses or constraints.
CASE_FIELDS = ("observed_failure", "fault", "repair", "verification")


@dataclass(frozen=True)
class KnowledgeItem:
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        item_id: str,
        version: int,
        applicability: Mapping[str, Any],
        card: Mapping[str, Any],
        example: Mapping[str, Any] | None,
        evidence: Mapping[str, Any],
        author: Mapping[str, Any],
    ) -> "KnowledgeItem":
        reject_forbidden(applicability, where="knowledge.applicability")
        reject_forbidden(card, where="knowledge.card")
        reject_forbidden(example or {}, where="knowledge.example")
        reject_forbidden(evidence, where="knowledge.evidence")
        bug_type = applicability.get("bug_type")
        if bug_type not in BUG_TYPES:
            raise KnowledgeValidationError("knowledge bug_type is not in taxonomy")
        ObservableProfile(
            status=dict(applicability.get("status") or {}),
            causal=dict(applicability.get("causal") or {}),
        )
        if set(card) != set(CARD_FIELDS):
            raise KnowledgeValidationError(f"knowledge card must hold exactly {CARD_FIELDS}")
        cases = card["cases"]
        if not isinstance(cases, list) or not cases:
            raise KnowledgeValidationError("knowledge card needs at least one case")
        for case in cases:
            missing = [key for key in CASE_FIELDS if not isinstance(case, Mapping) or key not in case]
            if missing:
                raise KnowledgeValidationError(f"case missing: {missing}")
            if not (case["fault"].get("faulty_lines") or case["repair"].get("fixed_lines")):
                raise KnowledgeValidationError("case needs the verified change")
        if not evidence.get("source_episode_hashes"):
            raise KnowledgeValidationError("knowledge needs source episodes")
        payload = {
            "schema_version": KNOWLEDGE_SCHEMA_VERSION,
            "item_id": str(item_id),
            "version": int(version),
            "applicability": deepcopy(dict(applicability)),
            "card": deepcopy(dict(card)),
            "example": deepcopy(dict(example)) if example else None,
            "evidence": deepcopy(dict(evidence)),
            "author": deepcopy(dict(author)),
        }
        return cls(_hashed(payload, "item_hash"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "KnowledgeItem":
        body = {k: v for k, v in payload.items() if k != "item_hash"}
        if hash_payload(body) != payload.get("item_hash"):
            raise KnowledgeValidationError("knowledge item hash mismatch")
        return cls(deepcopy(dict(payload)))

    @property
    def item_id(self) -> str:
        return str(self.payload["item_id"])

    @property
    def version(self) -> int:
        return int(self.payload["version"])

    @property
    def item_hash(self) -> str:
        return str(self.payload["item_hash"])

    @property
    def bug_type(self) -> str:
        return str(self.payload["applicability"]["bug_type"])

    @property
    def applicability_profile(self) -> ObservableProfile:
        app = self.payload["applicability"]
        return ObservableProfile(
            status=dict(app.get("status") or {}),
            causal=dict(app.get("causal") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self.payload))


@dataclass(frozen=True)
class KnowledgeBundle:
    """The knowledge shown to Blue for one case, with its selection record."""

    mode: str
    items: tuple[Mapping[str, Any], ...]
    matches: tuple[Mapping[str, Any], ...]
    query_profile_hash: str
    type_posterior: Mapping[str, float]
    usage_note: str
    selection_detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in {"none", "static", "matched", "shuffled"}:
            raise KnowledgeValidationError(f"unknown knowledge mode: {self.mode}")
        for item in self.items:
            reject_forbidden(item, where="bundle.item")

    @property
    def is_empty(self) -> bool:
        return not self.items

    def prompt_payload(self) -> dict[str, Any]:
        """Exactly what the Blue agent sees: no scores, no rationale."""
        return {
            "usage_note": self.usage_note,
            "items": [deepcopy(dict(item)) for item in self.items],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "mode": self.mode,
            "items": [deepcopy(dict(item)) for item in self.items],
            "matches": [deepcopy(dict(item)) for item in self.matches],
            "query_profile_hash": self.query_profile_hash,
            "type_posterior": dict(sorted(self.type_posterior.items())),
            "usage_note": self.usage_note,
            "selection_detail": deepcopy(dict(self.selection_detail)),
        }

    @property
    def bundle_hash(self) -> str:
        return hash_payload(self.to_dict())

