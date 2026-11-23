"""Candidate selection and rollback under incomplete verification evidence.

The cases below are the acceptance scenarios for the selection module: a good
candidate followed by a worse one, a pass whose log went missing, a run with no
valid candidate at all, and a duplicated patch. They are deliberately written
without a simulator: the module's contract is about evidence grading, not about
reproducing tool behaviour.
"""
from __future__ import annotations

import pytest

from r3e.knowledge import (
    CandidateEvidence,
    LastCandidateSelector,
    SelectionDecision,
    VerifiedBestSelector,
    replay,
)
from r3e.knowledge.selection import (
    ACCEPT,
    COMPILE_ERROR,
    CONTINUE,
    FUNC_FAIL,
    PASS,
    RERUN,
    ROLLBACK,
    STOP,
    TIMEOUT,
    UNVERIFIED,
)


def _ok(cid: str, **kw) -> CandidateEvidence:
    return CandidateEvidence(candidate_id=cid, **kw)


def _compile_error(cid: str) -> CandidateEvidence:
    return CandidateEvidence(candidate_id=cid, compile_ok=False, sim_passed=False)


def _func_fail(cid: str) -> CandidateEvidence:
    return CandidateEvidence(candidate_id=cid, compile_ok=True, sim_passed=False)


def test_status_grading_covers_every_evidence_kind():
    assert _ok("a").status == PASS
    assert _compile_error("b").status == COMPILE_ERROR
    assert _func_fail("c").status == FUNC_FAIL
    assert CandidateEvidence(candidate_id="d", timed_out=True).status == TIMEOUT
    # A reported pass without its log is not verified evidence.
    assert CandidateEvidence(candidate_id="e", has_log=False).status == UNVERIFIED


def test_verified_pass_is_accepted_and_kept_over_a_later_failure():
    selector = VerifiedBestSelector()
    assert selector.observe(_compile_error("c0")).action == CONTINUE
    accepted = selector.observe(_ok("c1"))
    assert accepted.action == ACCEPT and accepted.candidate_id == "c1"
    rolled_back = selector.observe(_func_fail("c2"))
    assert rolled_back.action == ROLLBACK and rolled_back.rollback_to == "c1"
    assert selector.best == "c1"
    assert selector.finalize().candidate_id == "c1"


def test_last_candidate_baseline_loses_the_verified_pass():
    selector = LastCandidateSelector()
    selector.observe(_ok("c1"))
    selector.observe(_func_fail("c2"))
    final = selector.finalize()
    assert final.candidate_id == "c2"
    assert final.detail["status"] == FUNC_FAIL


def test_missing_log_is_demoted_and_rerequested_once():
    selector = VerifiedBestSelector(max_reruns=1)
    first = selector.observe(CandidateEvidence(candidate_id="c0", has_log=False))
    assert first.action == RERUN
    second = selector.observe(CandidateEvidence(candidate_id="c1", has_log=False))
    assert second.action == CONTINUE
    assert selector.best is None
    assert selector.finalize().action == STOP


def test_no_valid_candidate_ends_with_an_explicit_stop():
    selector = VerifiedBestSelector()
    for evidence in (_compile_error("c0"), _func_fail("c1"),
                     CandidateEvidence(candidate_id="c2", timed_out=True)):
        selector.observe(evidence)
    final = selector.finalize()
    assert final.action == STOP
    assert final.reason == "final_no_valid_candidate"
    assert final.candidate_id is None


def test_duplicate_patch_is_not_reverified():
    selector = VerifiedBestSelector()
    selector.observe(_ok("c0", patch_hash="p0"))
    dup = selector.observe(_ok("c1", patch_hash="p0"))
    assert dup.action == CONTINUE and dup.reason == "duplicate_patch"
    assert dup.detail["first_seen_as"] == "c0"


def test_timeout_is_never_a_pass():
    selector = VerifiedBestSelector()
    decision = selector.observe(CandidateEvidence(candidate_id="c0", timed_out=True))
    assert decision.action == CONTINUE
    assert selector.best is None


def test_replay_compares_both_selectors_on_the_same_attempts():
    attempts = [_compile_error("c0"), _ok("c1"), _func_fail("c2")]
    method = replay(VerifiedBestSelector(), attempts)
    baseline = replay(LastCandidateSelector(), attempts)
    assert method["final"]["candidate_id"] == "c1"
    assert baseline["final"]["candidate_id"] == "c2"
    assert method["selector"] == "verified_best"
    assert baseline["selector"] == "last_candidate"
    assert [d["action"] for d in method["decisions"]] == [CONTINUE, ACCEPT, ROLLBACK]


def test_decisions_and_state_are_json_serialisable():
    import json

    selector = VerifiedBestSelector()
    selector.observe(_ok("c0"))
    payload = {"state": selector.to_dict(), "final": selector.finalize().to_dict()}
    restored = json.loads(json.dumps(payload))
    assert restored["state"]["best"] == "c0"
    assert restored["final"]["action"] == ACCEPT


def test_selected_candidate_must_be_the_verified_one_not_the_ratio_best():
    """A high pass-ratio candidate that never ran clean must not win."""
    selector = VerifiedBestSelector()
    selector.observe(_ok("clean"))
    decision = selector.observe(CandidateEvidence(
        candidate_id="flaky", compile_ok=True, sim_passed=False, test_pass_ratio=0.99))
    assert decision.action == ROLLBACK
    assert selector.finalize().candidate_id == "clean"