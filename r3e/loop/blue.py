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
from typing import Any, Mapping, Sequence

from r3e.arena.conformance import AdapterConformanceGate
from r3e.blue.portfolio.openai_provider import OpenAICompatibleCandidateProvider
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
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload, read_json

from .budget import BudgetedClient
from .corpus import Challenge, merge_clusters
from .sim import Simulator, Verdict


def feedback_summary(feedback: VisibleFeedback, *, limit: int = 4) -> dict[str, Any]:
    """The visible-test evidence shown to Blue for the current design."""
    if not feedback.compile_ok:
        return {"stage": "compile", "message": feedback.compile_message[:800]}
    return {
        "stage": "functional",
        "first_divergences": [
            {"signal": d.signal, "first_cycle": d.first_cycle, "expected": d.expected,
             "observed": d.observed, "pattern": d.symptom}
            for d in feedback.divergences[:limit]
        ],
        "passing_outputs": list(feedback.passing_outputs)[:16],
        "cycles_compared": feedback.total_cycles,
    }


@dataclass
class BlueConfig:
    budget_k: int = 3
    escalation_k: int = 3
    lens_id: str = "generic_v1"
    max_evidence_chars: int = 6000


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
    deliveries: list[dict[str, Any]] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "mode": self.mode,
            "profile": self.profile,
            "bundle_hash": hash_payload(self.bundle),
            "shown_items": self.bundle["selection_detail"].get("shown_item_ids", []),
            "matches": self.bundle["matches"],
            "attempts": self.attempts,
            "solved_within_budget": self.solved_within_budget,
            "solved_by_escalation": self.solved_by_escalation,
            "episode_hash": self.episode.episode_hash,
            "deliveries": self.deliveries,
        }


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
        self.provider = OpenAICompatibleCandidateProvider(
            self.client, verifier_id="r3e-loop-icarus-tiered", verifier_version="1"
        )
        self.gate = AdapterConformanceGate(self.provider)
        self.policy = PolicyState.from_dict(read_json(
            self.project_root / "configs/base_policy/frozen_base_policy_v3.json"
        ))
        registry = read_json(self.project_root / "configs/blue/lens_registry_v1.json")
        lenses = registry["lenses"]
        lens = (lenses[self.config.lens_id] if isinstance(lenses, dict)
                else next(x for x in lenses if x["lens_id"] == self.config.lens_id))
        asset = self.project_root / lens["prompt_asset_path"]
        if hash_file(asset) != lens["prompt_asset_hash"]:
            raise RuntimeError("lens prompt asset hash mismatch")
        self.lens_instruction = asset.read_text(encoding="utf-8")
        self.lens_hash = lens["definition_hash"]

    def initial(self, challenge: Challenge) -> tuple[Verdict, Any]:
        verdict = self.simulator.verdict(challenge.buggy_rtl, challenge.carrier)
        profile = build_profile(verdict.feedback, analyze_rtl(challenge.buggy_rtl))
        return verdict, profile

    def _attempt(
        self, challenge: Challenge, *, index: int, seed: int, phase: str,
        initial: Verdict, history: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None, Verdict | None]:
        evidence = {
            "initial_visible_test_failure": feedback_summary(initial.feedback),
            "previous_attempts": [
                {"attempt": h["index"], "edit": h["edit"], "verdict": h["verdict_tier"],
                 "visible_feedback": h["feedback"]}
                for h in history
            ],
        }
        while len(str(evidence)) > self.config.max_evidence_chars and evidence["previous_attempts"]:
            evidence["previous_attempts"].pop(0)
        artifact = {
            "buggy_rtl_source": challenge.buggy_rtl,
            "buggy_rtl_hash": hash_payload(challenge.buggy_rtl),
            "top_module": challenge.carrier.top_module,
        }
        slot = {"slot_index": index, "lens_id": self.config.lens_id,
                "lens_hash": self.lens_hash, "candidate_seed": int(seed) * 100 + index}
        candidate_id = f"C_{hash_payload({'c': challenge.challenge_id, 's': seed, 'i': index}).split(':', 1)[1][:12]}_{index}"
        record = {"index": index, "phase": phase, "candidate_id": candidate_id}
        try:
            with self.budget.in_phase(phase):
                output = self.gate.validate("generate_blue_candidate", self.provider.generate_candidate(
                    policy=self.policy,
                    current_case_evidence=evidence,
                    current_case_artifact=artifact,
                    slot=slot,
                    prompt_asset=self.lens_instruction,
                    prompt_hash=hash_payload({"evidence": evidence, "artifact": hash_payload(artifact),
                                              "lens": self.lens_hash}),
                    candidate_id=candidate_id,
                ))
        except Exception as exc:  # malformed output or provider failure still consumes the attempt
            if type(exc).__name__ == "CallBudgetExceeded":
                raise
            record.update(edit="<invalid provider output>", verdict_tier="compile_fail",
                          feedback={"stage": "provider", "message": type(exc).__name__},
                          error=type(exc).__name__)
            return record, None, None
        rtl = output["patch_payload"]["replacement_rtl"]
        verdict = self.simulator.verdict(rtl, challenge.carrier)
        record.update(
            edit=str(output["patch_payload"].get("edit", ""))[:300],
            verdict_tier=verdict.tier,
            hidden=verdict.hidden,
            feedback=feedback_summary(verdict.feedback),
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
    ) -> Encounter:
        initial, profile = self.initial(challenge)
        if initial.visible_ok:
            raise ValueError(f"challenge {challenge.challenge_id} does not fail its visible test")
        posterior = inference.posterior(profile)
        bundle = build_bundle(mode, profile=profile, type_posterior=posterior, pool=list(pool),
                              matcher=matcher, case_id=challenge.challenge_id, seed=seed,
                              static_items=static_items)
        attempts: list[dict[str, Any]] = []
        passing: tuple[str, Verdict] | None = None
        start = len(self.client.deliveries)
        plan = [(phase, self.config.budget_k)]
        if allow_escalation and self.config.escalation_k > 0:
            plan.append(("escalation", self.config.escalation_k))
        with self.client.active(bundle):
            for stage, count in plan:
                for _ in range(count):
                    if passing is not None:
                        break
                    record, rtl, verdict = self._attempt(
                        challenge, index=len(attempts), seed=seed, phase=stage,
                        initial=initial, history=attempts,
                    )
                    attempts.append(record)
                    if verdict is not None and verdict.visible_ok:
                        passing = (rtl, verdict)
                if passing is not None:
                    break
        solved_within = passing is not None and attempts[-1]["phase"] != "escalation"
        episode = build_repair_episode(
            case_id=challenge.challenge_id,
            design_cluster=design_cluster or challenge.carrier.cluster_id,
            buggy_rtl=challenge.buggy_rtl,
            feedback=initial.feedback,
            attempts=[{k: a[k] for k in ("verdict_tier", "edit_class") if k in a} for a in attempts],
            passing_candidate_rtl=passing[0] if passing else None,
            passing_verdict_tier=passing[1].tier if passing else "visible_pass",
            provenance={
                "origin": challenge.origin,
                "carrier_id": challenge.carrier.carrier_id,
                "mode": mode,
                "bundle_hash": bundle.bundle_hash,
                "solved_phase": attempts[-1]["phase"] if passing else None,
                "attempt_count": len(attempts),
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
            deliveries=self.client.deliveries[start:],
        )


def cluster_map(challenges: Sequence[Challenge]) -> Mapping[str, str]:
    carriers = {c.carrier.carrier_id: c.carrier for c in challenges}
    return merge_clusters(list(carriers.values()))
