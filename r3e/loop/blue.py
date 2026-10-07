"""Blue encounter runner: knowledge-conditioned repair with visible feedback.

One encounter runs as follows:
- Compute the buggy design's visible feedback and observable profile.
- Select knowledge in the requested mode (none, static, matched or shuffled).
- Make up to ``budget_k`` repair attempts. Each attempt sees the visible
  feedback of the previous ones.
- If still unsolved and escalation is allowed, make up to ``escalation_k``
  further attempts. These are billed to the ``escalation`` phase, which counts
  as learning cost.

Every attempt is a real provider call through the unchanged
``OpenAICompatibleCandidateProvider``. The knowledge reaches it through
``KnowledgeInjectingClient``. The result includes a ``RepairEpisode`` built
only from the visible feedback and Blue's own candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from contextlib import nullcontext
from typing import Any, Mapping, Sequence

from r3e.knowledge import (
    KnowledgeInjectingClient,
    KnowledgeItem,
    KnowledgeMatcher,
    RepairEpisode,
    analyze_rtl,
    build_bundle,
    build_profile,
    build_repair_episode,
    classify_repair,
)
from r3e.knowledge.schema import VisibleFeedback
from r3e.knowledge.type_inference import BugTypeInference
from r3e.protocol.hashing import hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

from .blue_provider import BlueOutputViolation, BlueProvider
from .budget import PHASES, BudgetedClient
from .corpus import Challenge
from .population import attempt_record
from .sim import Simulator, Verdict


def feedback_summary(feedback: VisibleFeedback, *, limit: int = 4,
                     window: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The visible-test evidence shown to Blue for the current design.

    ``window`` (``evidence_window``) adds the cycles around the first mismatch
    with the applied inputs, so Blue can follow the behaviour instead of
    guessing from a single wrong value.
    """
    if not feedback.compile_ok:
        return {"stage": "compile", "message": feedback.compile_message[:800]}
    out = {
        "stage": "functional",
        "first_divergences": [
            {"signal": d.signal, "first_cycle": d.first_cycle, "expected": d.expected,
             "observed": d.observed, "pattern": d.symptom}
            for d in feedback.divergences[:limit]
        ],
        "passing_outputs": list(feedback.passing_outputs)[:16],
        "cycles_compared": feedback.total_cycles,
    }
    if window:
        out["cycle_window"] = dict(window)
    return out


@dataclass
class BlueConfig:
    budget_k: int = 3
    escalation_k: int = 3
    max_infra_retries: int = 2   # provider failures tolerated per encounter before it is inconclusive
    lens_id: str = "generic_v1"
    max_evidence_chars: int = 12000
    # Retrieved memory is a reference, offered only from this repair attempt on
    # (1 = every attempt). Blue's first attempt is its own reasoning on the
    # evidence alone, identical to the no-memory system, so memory cannot
    # change what Blue would do first; it can only add ideas after a failure.
    memory_from_attempt: int = 2
    # add the design's own register values to each row of the evidence window
    # (r3e/knowledge/register_trace.py); off by default until a paired test
    register_trace: bool = False
    # "full": Blue returns the complete repaired RTL; "edits": edits to the current
    # RTL (any size, appends and deletions included) or the complete RTL, as
    # Blue chooses (blue_provider.py); the lens then leaves out the return form
    answer_format: str = "full"
    # "full": the visible-test evidence; "none": only that the design fails its
    # test (an ablation, to measure how much Blue relies on the test evidence)
    evidence: str = "full"


@dataclass
class Encounter:
    challenge_id: str
    mode: str
    profile: dict[str, Any]
    bundle: dict[str, Any]
    attempts: list[dict[str, Any]]
    solved_within_budget: bool
    solved_by_escalation: bool
    episode: RepairEpisode
    inconclusive: bool = False
    infra_failures: int = 0
    deliveries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def repair_attempts(self) -> list[dict[str, Any]]:
        return [a for a in self.attempts if not a.get("infra_failure")]

    def accounting(self) -> dict[str, Any]:
        """What this repair cost: model calls, tokens, time, memory used, which attempt fixed it."""
        tries = [a for a in self.attempts if not a.get("infra_failure")]
        fixed_at = next((i + 1 for i, a in enumerate(tries)
                         if a.get("verdict_tier") in {"visible_pass", "hidden_pass", "formal_pass"}), None)
        delivered = (self.bundle["selection_detail"].get("shown_item_ids", [])
                     if any(a.get("memory_offered") for a in self.attempts) else [])
        return {
            "llm_calls": len([a for a in self.attempts if not a.get("reused")]),
            "repair_attempts": len(tries),
            "provider_failures": self.infra_failures,
            "fixed": self.solved_within_budget or self.solved_by_escalation,
            "fixed_at_attempt": fixed_at,
            "fixed_by_escalation": self.solved_by_escalation,
            "input_tokens": sum((a.get("tokens") or {}).get("input", 0) for a in self.attempts if not a.get("reused")),
            "output_tokens": sum((a.get("tokens") or {}).get("output", 0) for a in self.attempts if not a.get("reused")),
            "model_seconds": round(sum(a.get("seconds") or 0 for a in self.attempts if not a.get("reused")), 2),
            "memory_delivered": bool(delivered),
            "memory_items": list(delivered),
            "memory_offered_from_attempt": next((i + 1 for i, a in enumerate(tries) if a.get("memory_offered")), None),
        }

    def record(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "mode": self.mode,
            "profile": self.profile,
            "bundle_hash": hash_payload(self.bundle),
            # items Blue actually received (memory is offered from attempt
            # ``memory_from_attempt``; an encounter solved earlier saw none)
            "shown_items": (self.bundle["selection_detail"].get("shown_item_ids", [])
                            if any(a.get("memory_offered") for a in self.attempts) else []),
            "retrieved_items": self.bundle["selection_detail"].get("shown_item_ids", []),
            "matches": self.bundle["matches"],
            "attempts": self.attempts,
            "solved_within_budget": self.solved_within_budget,
            "solved_by_escalation": self.solved_by_escalation,
            "inconclusive": self.inconclusive,
            "infra_failures": self.infra_failures,
            "episode_hash": self.episode.episode_hash,
            "deliveries": self.deliveries,
            "accounting": self.accounting(),
        }


LENS_PATH = Path(__file__).resolve().parent / "assets" / "blue_lens_generic_v1.txt"
LENS_EDITS_PATH = Path(__file__).resolve().parent / "assets" / "blue_lens_generic_edits_v1.txt"


class BlueRunner:
    def __init__(
        self,
        *,
        json_client: Any,
        simulator: Simulator,
        project_root: Path,
        config: BlueConfig | None = None,
        max_calls: int = 0,
    ):
        self.config = config or BlueConfig()
        self.simulator = simulator
        self.project_root = Path(project_root)
        self.budget = json_client if isinstance(json_client, BudgetedClient) else BudgetedClient(json_client, max_calls=max_calls)
        self.client = KnowledgeInjectingClient(self.budget)
        self.provider = BlueProvider(self.client, answer_format=self.config.answer_format)
        if self.config.lens_id != "generic_v1":
            raise ValueError(f"unknown Blue lens {self.config.lens_id}")
        self.lens_instruction = (LENS_EDITS_PATH if self.config.answer_format == "edits"
                                 else LENS_PATH).read_text(encoding="utf-8")
        self.lens_hash = hash_payload({"lens_id": self.config.lens_id, "instruction": self.lens_instruction})
        # callable returning each case's usage track record (memory_usage.py), or None
        self.track_records = None
        # callable receiving one population record per attempt (population.py), or None
        self.population_sink = None

    def initial(self, challenge: Challenge) -> tuple[Verdict, Any]:
        verdict = self.simulator.verdict(challenge.buggy_rtl, challenge.carrier,
                                         registers=self.config.register_trace)
        profile = build_profile(verdict.feedback, analyze_rtl(challenge.buggy_rtl))
        return verdict, profile

    def _attempt(
        self, challenge: Challenge, *, index: int, seed: int, phase: str,
        initial: Verdict, history: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None, Verdict | None]:
        if self.config.evidence not in {"full", "none"}:
            raise ValueError(f"unknown evidence mode {self.config.evidence}")
        evidence = {
            "initial_visible_test_failure": (
                feedback_summary(initial.feedback, window=initial.window) if self.config.evidence == "full"
                else {"stage": "compile" if not initial.feedback.compile_ok else "functional",
                      "result": "the design fails its test; no further details are available"}),
            "previous_attempts": [
                {"attempt": h["index"], "edit": h["edit"], "verdict": h["verdict_tier"],
                 **({"visible_feedback": (dict(h["feedback"], cycle_window=h["window"])
                                          if h is history[-1] and h.get("window") else h["feedback"])}
                    if self.config.evidence == "full" else {})}
                for h in history
            ],
        }
        while len(str(evidence)) > self.config.max_evidence_chars and evidence["previous_attempts"]:
            evidence["previous_attempts"].pop(0)
        artifact = {
            "buggy_rtl_source": challenge.buggy_rtl,
            "buggy_rtl_hash": hash_payload(challenge.buggy_rtl),
            "top_module": challenge.carrier.top_module,
            **({"specification": challenge.carrier.spec} if challenge.carrier.spec else {}),
        }
        slot = {"slot_index": index, "lens_id": self.config.lens_id,
                "lens_hash": self.lens_hash, "candidate_seed": int(seed) * 100 + index}
        candidate_id = f"C_{hash_payload({'c': challenge.challenge_id, 's': seed, 'i': index}).split(':', 1)[1][:12]}_{index}"
        record = {"index": index, "phase": phase, "candidate_id": candidate_id}
        started = time.monotonic()
        if phase not in PHASES:  # a configuration error must stop the run, not pass as a failed repair
            raise ValueError(f"unknown phase {phase}")
        try:
            with self.budget.in_phase(phase):
                output = self.provider.generate_candidate(
                    evidence=evidence,
                    artifact=artifact,
                    slot=slot,
                    lens_instruction=self.lens_instruction,
                    prompt_hash=hash_payload({"evidence": evidence, "artifact": hash_payload(artifact),
                                              "lens": self.lens_hash}),
                    candidate_id=candidate_id,
                )
        except Exception as exc:
            if type(exc).__name__ == "CallBudgetExceeded":
                raise
            # Classification of a failed call:
            # - finish_reason == "length": the model spent its own output budget
            #   without answering. That is Blue failing this attempt under a
            #   fixed budget ("no_answer"), and it consumes the attempt.
            # - other provider/transport failures (timeouts, connection, 5xx):
            #   infrastructure; they do not consume a repair attempt.
            # - malformed output from the model: Blue's own failure.
            diagnostics = dict(getattr(exc, "diagnostics", {}) or {})
            truncated = diagnostics.get("finish_reason") == "length"
            infra = isinstance(exc, OpenAICompatibleProviderViolation) and not truncated
            tier = "provider_fail" if infra else "no_answer" if truncated else "compile_fail"
            message = ("previous attempt ran out of output budget before answering; reason more briefly"
                       if truncated else str(exc)[:300] if isinstance(exc, BlueOutputViolation)
                       else type(exc).__name__)
            record.update(edit="<no usable provider output>",
                          verdict_tier=tier,
                          tokens={"input": int(diagnostics.get("input_tokens") or 0),
                                  "output": int(diagnostics.get("output_tokens") or 0)},
                          seconds=round(time.monotonic() - started, 2),
                          infra_failure=infra,
                          feedback={"stage": "provider", "message": message},
                          error=type(exc).__name__,
                          error_message=str(exc)[:200],
                          diagnostics=diagnostics)
            return record, None, None
        rtl = output["patch_payload"]["replacement_rtl"]
        record["tokens"] = {"input": int(output["input_tokens"]), "output": int(output["output_tokens"])}
        record["answer_form"] = output["patch_payload"].get("answer_form", "full")
        record["seconds"] = round(time.monotonic() - started, 2)  # model call only
        verdict = self.simulator.verdict(rtl, challenge.carrier, registers=self.config.register_trace)
        record.update(
            edit=str(output["patch_payload"].get("edit", ""))[:300],
            verdict_tier=verdict.tier,
            hidden=verdict.hidden,
            feedback=feedback_summary(verdict.feedback),
            window=verdict.window,
            candidate_hash=hash_payload(rtl),
            command_hash=output.get("command_hash"),
        )
        if not verdict.visible_ok:
            record["edit_class"] = (
                "no_change" if rtl.strip() == challenge.buggy_rtl.strip()
                else classify_repair(challenge.buggy_rtl, rtl)["bug_type"]
            )
        return record, rtl, verdict

    def run(
        self,
        challenge: Challenge,
        *,
        mode: str,
        pool: Sequence[KnowledgeItem],
        inference: BugTypeInference,
        matcher: KnowledgeMatcher,
        seed: int,
        static_items: Sequence[KnowledgeItem] = (),
        allow_escalation: bool = True,
        phase: str = "blue_inference",
        design_cluster: str | None = None,
        reuse_first: Mapping[str, Any] | None = None,
    ) -> Encounter:
        """Repair ``challenge``; ``reuse_first`` continues from an already recorded
        failed first attempt (a fork: the same first attempt, then two branches)."""
        initial, profile = self.initial(challenge)
        if initial.visible_ok:
            raise ValueError(f"challenge {challenge.challenge_id} does not fail its visible test")
        posterior = inference.posterior(profile)
        bundle = build_bundle(mode, profile=profile, type_posterior=posterior, pool=list(pool),
                              matcher=matcher, case_id=challenge.challenge_id, seed=seed,
                              static_items=static_items,
                              track_records=self.track_records() if self.track_records else None)
        attempts: list[dict[str, Any]] = []
        passing: tuple[str, Verdict] | None = None
        infra_failures = 0
        inconclusive = False
        start = len(self.client.deliveries)
        plan = [(phase, self.config.budget_k)]
        if allow_escalation and self.config.escalation_k > 0:
            plan.append(("escalation", self.config.escalation_k))
        if reuse_first is not None:
            if reuse_first.get("verdict_tier") in {"visible_pass", "hidden_pass", "formal_pass"}:
                raise ValueError("a fork continues only from a failed first attempt")
            attempts.append({**dict(reuse_first), "reused": True, "memory_offered": False})
        for stage_index, (stage, count) in enumerate(plan):
            used = 1 if (reuse_first is not None and stage_index == 0) else 0
            while used < count and passing is None and not inconclusive:
                history = [a for a in attempts if not a.get("infra_failure")]
                offer = len(history) + 1 >= self.config.memory_from_attempt
                with (self.client.active(bundle) if offer else nullcontext()):
                    record, rtl, verdict = self._attempt(
                        challenge, index=len(attempts), seed=seed, phase=stage, initial=initial,
                        history=history,
                    )
                record["memory_offered"] = bool(offer and not bundle.is_empty)
                attempts.append(record)
                if self.population_sink is not None and not record.get("infra_failure"):
                    delivered = bundle.selection_detail.get("shown_item_ids", []) if record["memory_offered"] else []
                    self.population_sink(attempt_record(challenge=challenge, profile=profile.to_dict(),
                                                        record=record, rtl=rtl, verdict=verdict,
                                                        history_len=len(history), seed=seed,
                                                        delivered=list(delivered)))
                if record.get("infra_failure"):
                    infra_failures += 1
                    inconclusive = infra_failures > self.config.max_infra_retries
                    continue
                used += 1
                if verdict is not None and verdict.visible_ok:
                    passing = (rtl, verdict)
            if passing is not None or inconclusive:
                break
        if passing is not None:
            inconclusive = False
        repair_attempts = [a for a in attempts if not a.get("infra_failure")]
        solved_within = passing is not None and repair_attempts[-1]["phase"] != "escalation"
        episode = build_repair_episode(
            case_id=challenge.challenge_id,
            design_cluster=design_cluster or challenge.carrier.cluster_id,
            buggy_rtl=challenge.buggy_rtl,
            feedback=initial.feedback,
            attempts=[] if inconclusive else [
                {k: a[k] for k in ("verdict_tier", "edit_class", "edit") if k in a} for a in repair_attempts],
            passing_candidate_rtl=passing[0] if passing else None,
            passing_verdict_tier=passing[1].tier if passing else "visible_pass",
            provenance={
                "origin": challenge.origin,
                "carrier_id": challenge.carrier.carrier_id,
                "mode": mode,
                "bundle_hash": bundle.bundle_hash,
                "solved_phase": repair_attempts[-1]["phase"] if passing else None,
                "attempt_count": len(repair_attempts),
                "infra_failures": infra_failures,
                "failure_window": initial.window,
                "memory_offered": any(a.get("memory_offered") for a in repair_attempts),
            },
        )
        return Encounter(
            challenge_id=challenge.challenge_id,
            mode=mode,
            profile=profile.to_dict(),
            bundle=bundle.to_dict(),
            attempts=attempts,
            solved_within_budget=solved_within,
            solved_by_escalation=passing is not None and not solved_within,
            episode=episode,
            inconclusive=inconclusive,
            infra_failures=infra_failures,
            deliveries=self.client.deliveries[start:],
        )


def first_attempt(enc: Encounter) -> dict[str, Any] | None:
    """The first non-infrastructure attempt of an encounter (the fork point)."""
    return next((a for a in enc.attempts if not a.get("infra_failure")), None)
