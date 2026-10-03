"""Build a RepairEpisode from one real Blue encounter.

Inputs are the current buggy RTL, the visible feedback on the buggy design,
Blue's attempts with their verdict tiers, and (optionally) Blue's own
candidate RTL that passed. Golden RTL is never an input.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from r3e.protocol.hashing import hash_payload

from .anonymize import anonymize_pair, identifier_roles
from .diff_classifier import classify_repair
from .profile import build_profile
from .schema import VERIFIED_REPAIR_TIERS, RepairEpisode, VisibleFeedback
from .structure import analyze_rtl


def build_repair_episode(
    *,
    case_id: str,
    design_cluster: str,
    buggy_rtl: str,
    feedback: VisibleFeedback,
    attempts: Sequence[Mapping[str, Any]],
    passing_candidate_rtl: str | None = None,
    passing_verdict_tier: str = "visible_pass",
    provenance: Mapping[str, Any] | None = None,
) -> RepairEpisode:
    structure = analyze_rtl(buggy_rtl)
    profile = build_profile(feedback, structure)
    verified = None
    if passing_candidate_rtl is not None:
        if passing_verdict_tier not in VERIFIED_REPAIR_TIERS:
            raise ValueError("passing candidate needs a passing verdict tier")
        classification = classify_repair(
            buggy_rtl,
            passing_candidate_rtl,
            structure=structure,
            failing_signals=[d.signal for d in feedback.divergences],
        )
        raw_before = classification.pop("before_lines")
        raw_after = classification.pop("after_lines")
        before, after, renamed = anonymize_pair(raw_before, raw_after, structure)
        verified = {
            "source": "blue_candidate",
            "verdict_tier": passing_verdict_tier,
            "candidate_rtl_hash": hash_payload(passing_candidate_rtl),
            "classification": classification,
            "anonymized_example": {
                "before": before,
                "after": after,
                "renamed_identifiers": renamed,
            },
            # the verified change as it was, with each identifier's role, so a
            # later case record can quote it faithfully
            "lines": {
                "before": list(raw_before),
                "after": list(raw_after),
                "identifier_roles": identifier_roles([*raw_before, *raw_after], structure),
            },
        }
    outcome = "resolved" if verified is not None else (
        "unresolved" if attempts else "inconclusive"
    )
    episode_id = "EP_" + hash_payload({
        "case_id": case_id,
        "buggy": hash_payload(buggy_rtl),
        "attempts": [dict(a) for a in attempts],
    }).split(":", 1)[1][:16]
    return RepairEpisode.create(
        episode_id=episode_id,
        case_id=str(case_id),
        design_cluster=str(design_cluster),
        buggy_rtl_hash=hash_payload(buggy_rtl),
        feedback=feedback.to_dict(),
        profile=profile.to_dict(),
        attempts=[dict(a) for a in attempts],
        verified_repair=verified,
        outcome=outcome,
        provenance=dict(provenance or {}),
    )
