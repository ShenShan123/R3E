"""Case memory: verified repairs stored as causal chains, never as rules.

Each record answers, from one verified repair episode and nothing else:

- **observed_failure**: what the visible test showed (wrong outputs, how
  often, the first wrong cycle with its inputs);
- **fault**: where the faulty construct was (block kind, the assigned
  signal's role, whether it sat in the failing output's logic) and the
  faulty lines as they were;
- **repair**: the lines after the verified fix, and Blue's own explanation
  of that fix, verbatim;
- **did_not_work**: the earlier attempts in that encounter that failed;
- **verification**: how the fix was verified and how many attempts it took.

There are no authored principles, hypotheses or constraints. Nothing is
inferred beyond what the episode recorded, so a record cannot assert a rule
the evidence does not show.

Episodes are grouped for retrieval by the verified change type and the
failure's symptom and causal position. A group becomes one knowledge item
whose applicability is the consensus profile of its members (only the
features they all share), and whose card holds up to ``max_cases`` of the
members' causal chains, preferring distinct designs. Code is quoted as it
was in the past design, with each identifier's role (input, state, ...), so
Blue can relate it to the current design without renamed text.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from r3e.protocol.hashing import hash_payload

from .schema import KnowledgeItem, RepairEpisode


AUTHOR = {"author_id": "case-memory", "author_version": "1"}


def _consensus(profiles: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Features with the same value in every member."""
    if not profiles:
        return {}
    shared = dict(profiles[0])
    for other in profiles[1:]:
        shared = {k: v for k, v in shared.items() if other.get(k) == v}
    return shared


def _observed(episode: RepairEpisode) -> dict[str, Any]:
    window = (episode.get("provenance") or {}).get("failure_window") or {}
    feedback = episode.get("feedback") or {}
    out: dict[str, Any] = {}
    wrong = window.get("wrong_cycles") or {}
    if wrong:
        total = window.get("cycles_compared")
        out["wrong_outputs"] = {name: {"wrong_cycles": info.get("count"), "of_cycles": total,
                                       "first_wrong_cycles": info.get("first_cycles", [])[:4]}
                                for name, info in wrong.items()}
    else:
        out["wrong_outputs"] = {d["signal"]: {"first_wrong_cycle": d.get("first_cycle")}
                                for d in feedback.get("divergences", [])}
    first_rows = [r for r in window.get("rows", []) if any(not o.get("ok", True) for o in r.get("outputs", {}).values())]
    if first_rows:
        out["first_wrong_cycle"] = first_rows[0]
    if window.get("stimulus_notes"):
        out["stimulus_notes"] = list(window["stimulus_notes"])
    out["passing_outputs"] = list(feedback.get("passing_outputs") or [])[:8]
    return out


def causal_chain(episode: RepairEpisode) -> dict[str, Any]:
    repair = episode.get("verified_repair") or {}
    cls = repair.get("classification") or {}
    lines = repair.get("lines") or {}
    attempts = list(episode.get("attempts") or [])
    provenance = episode.get("provenance") or {}
    explanation = next((str(a.get("edit") or "") for a in reversed(attempts)
                        if a.get("verdict_tier") in {"visible_pass", "hidden_pass", "formal_pass"}), "")
    failed = [{"edit": str(a.get("edit") or "")[:200], "result": a.get("verdict_tier")}
              for a in attempts if a.get("verdict_tier") not in {"visible_pass", "hidden_pass", "formal_pass"}]
    return {
        "observed_failure": _observed(episode),
        "fault": {
            "block_kind": cls.get("block_kind"),
            "assigned_signal_role": cls.get("assigned_signal_role"),
            "in_failing_output_logic": cls.get("edit_in_failing_cone"),
            "faulty_lines": list(lines.get("before") or (repair.get("anonymized_example") or {}).get("before", [])),
        },
        "repair": {
            "fixed_lines": list(lines.get("after") or (repair.get("anonymized_example") or {}).get("after", [])),
            "explanation_by_repairer": explanation[:300],
            "edit_scope": cls.get("edit_scope"),
        },
        "identifier_roles": dict(lines.get("identifier_roles") or {}),
        "did_not_work": failed[-3:],
        "verification": {"result": repair.get("verdict_tier"),
                         "attempts_needed": provenance.get("attempt_count", len(attempts)),
                         "needed_escalation": provenance.get("solved_phase") == "escalation"},
    }


class CaseMemoryAuthor:
    author_id = AUTHOR["author_id"]

    def __init__(self, *, max_cases: int = 3):
        self.max_cases = max_cases

    @staticmethod
    def group_key(episode: RepairEpisode) -> tuple[str, str, str, str]:
        profile = episode.profile
        return (str(episode.verified_bug_type), str(profile.status.get("symptom", "unknown")),
                str(profile.causal.get("output_driver_kind", "unknown")),
                str(profile.causal.get("register_distance", "unknown")))

    def author(self, episodes: Iterable[RepairEpisode]) -> list[KnowledgeItem]:
        groups: dict[tuple[str, ...], list[RepairEpisode]] = defaultdict(list)
        for episode in episodes:
            if episode.verified_bug_type is not None:
                groups[self.group_key(episode)].append(episode)
        return [self._item(members) for _, members in sorted(groups.items())]

    def _pick(self, members: list[RepairEpisode]) -> list[RepairEpisode]:
        """Up to ``max_cases`` members, one per design cluster first."""
        ordered = sorted(members, key=lambda e: e.episode_hash)
        picked, seen = [], set()
        for episode in ordered:
            if episode.get("design_cluster") not in seen:
                picked.append(episode)
                seen.add(episode.get("design_cluster"))
        picked += [e for e in ordered if e not in picked]
        return picked[: self.max_cases]

    def _item(self, members: list[RepairEpisode]) -> KnowledgeItem:
        members = sorted(members, key=lambda e: e.episode_hash)
        applicability = {
            "bug_type": members[0].verified_bug_type,
            "status": _consensus([m.profile.status for m in members]),
            "causal": _consensus([m.profile.causal for m in members]),
        }
        return KnowledgeItem.create(
            item_id="K_" + hash_payload(applicability).split(":", 1)[1][:16],
            version=1,
            applicability=applicability,
            card={"cases": [causal_chain(e) for e in self._pick(members)]},
            example=None,
            evidence={"support": len(members),
                      "source_episode_hashes": [m.episode_hash for m in members],
                      "design_clusters": sorted({str(m.get("design_cluster")) for m in members})},
            author=dict(AUTHOR),
        )
