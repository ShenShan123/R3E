"""Evidence-graded selection and rollback over repair candidates.

Iterative RTL debugging generates several candidate patches per case (MEIC,
ICCAD 2024, is the reference flow). Deciding *which* candidate to keep is a
separate concern from generating them, and it is where an LLM agent most often
loses work it has already done:

- a candidate that passed every test is later replaced by an unverified one,
  because the loop simply keeps the last edit;
- a run whose log is missing is counted as a success, because the simulator
  printed ``PASS`` before the artefact was lost;
- a timeout (or an unknown tool state) is recorded as a functional pass;
- the same patch is re-verified several times, wasting simulation budget;
- a case with no valid candidate exits silently instead of reporting failure.

This module keeps the decision on observable verification evidence. It does not
generate candidates, does not prompt a model and does not touch the filesystem:
callers feed it one :class:`CandidateEvidence` per attempt and act on the
returned :class:`SelectionDecision`.

Evidence grades, strongest first:

``PASS``
    Compiles, simulation passed *and* the run log is present.
``COMPILE_ERROR`` / ``FUNC_FAIL``
    Reproduced, actionable failures.
``TIMEOUT``
    Inconclusive by construction; never a pass.
``UNVERIFIED``
    Simulation claimed a pass but the log is missing, or the simulator did not
    run. Demoted, not accepted.

Two selectors are provided for A/B evaluation: :class:`VerifiedBestSelector`
(the method) and :class:`LastCandidateSelector` (the "keep the last edit"
baseline that the failures above describe).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from r3e.protocol.hashing import hash_payload

SELECTION_SCHEMA_VERSION = "r3e-candidate-selection-v1"

PASS = "pass"
COMPILE_ERROR = "compile_error"
FUNC_FAIL = "func_fail"
TIMEOUT = "timeout"
UNVERIFIED = "unverified"

EVIDENCE_GRADES = (PASS, COMPILE_ERROR, FUNC_FAIL, TIMEOUT, UNVERIFIED)

ACCEPT = "accept"
ROLLBACK = "rollback"
RERUN = "rerun"
CONTINUE = "continue"
STOP = "stop"

ACTIONS = (ACCEPT, ROLLBACK, RERUN, CONTINUE, STOP)

# Reason codes carried into the ledger; stable strings, safe to aggregate.
R_VERIFIED_PASS = "verified_pass"
R_DEMOTED_NO_LOG = "demoted_no_log"
R_RERUN_REQUESTED = "rerun_requested"
R_STILL_UNVERIFIED = "still_unverified"
R_DUPLICATE_PATCH = "duplicate_patch"
R_FAILED_ROLLBACK = "failed_rollback"
R_FAILED_NO_BEST = "failed_no_best"
R_FINAL_BEST = "final_best"
R_FINAL_NO_VALID = "final_no_valid_candidate"
R_FINAL_KEEP_LAST = "final_keep_last"


@dataclass(frozen=True)
class CandidateEvidence:
    """One attempt's verification evidence, as the runner observed it.

    ``has_log`` is deliberately separate from ``sim_passed``: a simulation that
    reports a pass while its log is missing cannot be re-checked afterwards, so
    it must not be treated as verified.
    """

    candidate_id: str
    round_index: int = 0
    patch_hash: str = ""
    compile_ok: bool = True
    sim_passed: bool = True
    has_log: bool = True
    timed_out: bool = False
    test_pass_ratio: float = 1.0

    @property
    def status(self) -> str:
        """The evidence grade this attempt actually earns."""
        if self.timed_out:
            return TIMEOUT
        if not self.compile_ok:
            return COMPILE_ERROR
        if not self.sim_passed:
            return FUNC_FAIL
        if not self.has_log:
            return UNVERIFIED
        return PASS

    @property
    def verified_pass(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "round_index": self.round_index,
            "patch_hash": self.patch_hash,
            "compile_ok": self.compile_ok,
            "sim_passed": self.sim_passed,
            "has_log": self.has_log,
            "timed_out": self.timed_out,
            "test_pass_ratio": self.test_pass_ratio,
            "status": self.status,
        }

    @property
    def evidence_hash(self) -> str:
        return hash_payload(self.to_dict())


@dataclass(frozen=True)
class SelectionDecision:
    """What the selector decided for one attempt, and why."""

    action: str
    candidate_id: str | None
    rollback_to: str | None
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "candidate_id": self.candidate_id,
            "rollback_to": self.rollback_to,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


class VerifiedBestSelector:
    """Keep the best *verified* candidate; demote unverifiable claims.

    Rules:

    R1  Acceptance requires a verified pass: compile OK, simulation passed and
        the run log present.
    R2  Monotonicity: once a verified pass exists it is never replaced by a
        candidate that is not itself a verified pass.
    R3  Failure of a later candidate rolls back to the last verified pass
        instead of silently keeping the failure.
    R4  Timeouts and unknown states are not passes; an unverifiable attempt is
        re-run at most ``max_reruns`` times and then dropped.
    R5  A patch hash already seen is not re-verified.
    R6  Ending with no verified pass yields ``STOP``, not a success claim.
    """

    name = "verified_best"

    def __init__(self, *, max_reruns: int = 1) -> None:
        self._max_reruns = int(max_reruns)
        self._best: str | None = None
        self._seen: dict[str, str] = {}
        self._reruns = 0
        self._history: list[CandidateEvidence] = []

    @property
    def best(self) -> str | None:
        return self._best

    def observe(self, evidence: CandidateEvidence) -> SelectionDecision:
        self._history.append(evidence)
        status = evidence.status

        if evidence.patch_hash and evidence.patch_hash in self._seen:
            first = self._seen[evidence.patch_hash]
            return SelectionDecision(
                CONTINUE, None, self._best, R_DUPLICATE_PATCH,
                {"status": status, "first_seen_as": first},
            )
        if evidence.patch_hash:
            self._seen[evidence.patch_hash] = evidence.candidate_id

        if status == UNVERIFIED:
            if self._reruns < self._max_reruns:
                self._reruns += 1
                return SelectionDecision(
                    RERUN, evidence.candidate_id, self._best, R_RERUN_REQUESTED,
                    {"status": status, "reruns_used": self._reruns},
                )
            return SelectionDecision(
                CONTINUE, None, self._best, R_STILL_UNVERIFIED,
                {"status": status, "reruns_used": self._reruns},
            )

        if status == PASS:
            previous = self._best
            self._best = evidence.candidate_id
            return SelectionDecision(
                ACCEPT, evidence.candidate_id, None, R_VERIFIED_PASS,
                {"status": status, "previous_best": previous,
                 "test_pass_ratio": evidence.test_pass_ratio},
            )

        if self._best is not None:
            return SelectionDecision(
                ROLLBACK, None, self._best, R_FAILED_ROLLBACK,
                {"status": status, "rolled_back_from": evidence.candidate_id},
            )
        return SelectionDecision(
            CONTINUE, None, None, R_FAILED_NO_BEST, {"status": status},
        )

    def finalize(self) -> SelectionDecision:
        if self._best is not None:
            return SelectionDecision(ACCEPT, self._best, None, R_FINAL_BEST,
                                     {"status": PASS, "attempts": len(self._history)})
        return SelectionDecision(
            STOP, None, None, R_FINAL_NO_VALID,
            {"attempts": len(self._history),
             "statuses": [e.status for e in self._history]},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SELECTION_SCHEMA_VERSION,
            "selector": self.name,
            "max_reruns": self._max_reruns,
            "best": self._best,
            "reruns": self._reruns,
            "history": [e.to_dict() for e in self._history],
        }


class LastCandidateSelector:
    """Baseline: keep the last edit, whatever its evidence says.

    This mirrors the behaviour the iterative loop defaults to when no selection
    policy is specified, and is the control arm for :class:`VerifiedBestSelector`.
    """

    name = "last_candidate"

    def __init__(self) -> None:
        self._last: str | None = None
        self._history: list[CandidateEvidence] = []

    def observe(self, evidence: CandidateEvidence) -> SelectionDecision:
        self._history.append(evidence)
        self._last = evidence.candidate_id
        return SelectionDecision(
            ACCEPT, evidence.candidate_id, None, R_FINAL_KEEP_LAST,
            {"status": evidence.status},
        )

    def finalize(self) -> SelectionDecision:
        if self._last is None:
            return SelectionDecision(STOP, None, None, R_FINAL_NO_VALID,
                                     {"attempts": 0})
        return SelectionDecision(ACCEPT, self._last, None, R_FINAL_KEEP_LAST,
                                 {"status": self._history[-1].status})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SELECTION_SCHEMA_VERSION,
            "selector": self.name,
            "last": self._last,
            "history": [e.to_dict() for e in self._history],
        }


SELECTORS = {VerifiedBestSelector.name: VerifiedBestSelector,
             LastCandidateSelector.name: LastCandidateSelector}


def replay(selector, attempts) -> dict[str, Any]:
    """Run one selector over a fixed candidate sequence and summarise the outcome.

    ``attempts`` are :class:`CandidateEvidence` values (or mappings with the same
    fields). Returns the decision log plus the final selection, so a course
    experiment can compare two selectors on the same replay.
    """
    decisions = []
    for attempt in attempts:
        evidence = attempt if isinstance(attempt, CandidateEvidence) else CandidateEvidence(**attempt)
        decisions.append(selector.observe(evidence).to_dict())
    final = selector.finalize()
    return {"selector": selector.name, "decisions": decisions,
            "final": final.to_dict(), "state": selector.to_dict()}