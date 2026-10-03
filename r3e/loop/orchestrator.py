"""The closed red–blue loop.

One round runs these steps:
1. Red proposes challenges on *discovery* carriers; the admission gate filters them.
2. Blue repairs each admitted challenge with matched knowledge (``budget_k``
   attempts, then up to ``escalation_k`` escalation attempts as learning cost).
3. Every encounter becomes a ``RepairEpisode`` in the ledger.
4. Candidate knowledge is authored from all verified episodes so far. New
   content enters the store as ``candidate``.
5. Each candidate gets paired qualification on the frozen *qualification* set,
   then is promoted or rejected.
6. The usage monitor suspends active items whose use correlates with failure.
7. A snapshot records the active knowledge and cost, and the run checkpoints.

The *holdout* split is never touched inside the loop. It is evaluated only
afterwards, by ``evaluate.evaluate_holdout``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from r3e.knowledge import CaseMemoryAuthor, KnowledgeItem
from r3e.protocol.hashing import hash_payload

from .blue import BlueConfig, BlueRunner
from .budget import BudgetedClient
from .corpus import Carrier, Challenge
from .qualification import (
    MonitorConfig,
    QualificationConfig,
    QualificationSet,
    monitor_usage,
    qualify_candidate,
)
from .memory_usage import record_memory_usage, track_record_view, usage_stats
from .red import RedAgent
from .sim import Simulator
from .state import RunState


@dataclass
class LoopConfig:
    rounds: int = 3
    proposals_per_round: int = 6
    red_mode: str = "aware"
    seed: int = 0
    max_candidates_per_round: int = 4
    learning: bool = True          # False: Red + Blue only (no authoring/qualification/monitor)
    frozen_mutants_per_carrier: int = 2
    blue: BlueConfig = field(default_factory=BlueConfig)
    qualification: QualificationConfig = field(default_factory=QualificationConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)


def frozen_challenges(
    carriers: Sequence[Carrier],
    public: Sequence[Challenge],
    simulator: Simulator,
    *,
    per_carrier: int,
    seed: int,
) -> list[Challenge]:
    """Public challenges plus deterministic random-operator mutants (no model calls).

    Mutants mix one- and two-edit bugs, so the frozen qualification and
    holdout sets are not dominated by cases Blue always solves (which would
    leave knowledge no room to show a gain).
    """
    ids = {c.carrier_id for c in carriers}
    out = [ch for ch in public if ch.carrier.carrier_id in ids]
    red = RedAgent(mode="random", client=None, simulator=simulator, seed=seed, random_max_edits=2)
    dummy = None
    for carrier in carriers:
        made, tries = 0, 0
        while made < per_carrier and tries < 6 * per_carrier:
            tries += 1
            proposal = red.propose(carrier, state=dummy, seed=seed + tries, round_index=-1)
            if proposal.challenge is not None:
                out.append(proposal.challenge)
                made += 1
    return out


def _next_version(state: RunState, item: KnowledgeItem) -> KnowledgeItem | None:
    """Return the item to store (new id or new version), or None if unchanged."""
    existing = [i for i in state.store.items_with_status(
        "candidate", "qualified", "active", "rejected", "suspended", "retired")
        if i.item_id == item.item_id]
    if not existing:
        return item
    latest = max(existing, key=lambda i: i.version)
    # Evidence (support counts, episode ids) grows every round; a new version,
    # and so a new paired qualification, is warranted only when what Blue
    # would actually be shown changes.
    ignored = {"version", "item_hash", "evidence"}
    same = {k: v for k, v in latest.payload.items() if k not in ignored}
    new = {k: v for k, v in item.payload.items() if k not in ignored}
    if same == new:
        return None
    body = item.to_dict()
    return KnowledgeItem.create(
        item_id=body["item_id"], version=latest.version + 1,
        applicability=body["applicability"], card=body["card"], example=body["example"],
        evidence=body["evidence"], author=body["author"],
    )


class ClosedLoop:
    def __init__(
        self,
        *,
        config: LoopConfig,
        state: RunState,
        splits: dict[str, list[Carrier]],
        public_challenges: Sequence[Challenge],
        blue: BlueRunner,
        red_client: BudgetedClient | None,
        simulator: Simulator,
        qualification_challenges: Sequence[Challenge] | None = None,
    ):
        self.config = config
        self.state = state
        self.splits = splits
        self.blue = blue
        self.simulator = simulator
        self.red = RedAgent(mode=config.red_mode, client=red_client, simulator=simulator, seed=config.seed)
        self.author = CaseMemoryAuthor()
        # every case Blue is offered carries its usage track record (data only)
        blue.track_records = lambda: {k: track_record_view(v) for k, v in usage_stats(state).items()}
        # every attempt (failed branches included) goes to the population ledger
        self._round = -1
        blue.population_sink = lambda rec: state.append("population", {"round": self._round, **rec})
        if qualification_challenges is not None:  # e.g. a difficulty-screened set (hardsets.py)
            qual = list(qualification_challenges)
        else:
            qual = frozen_challenges(splits["qualification"], public_challenges, simulator,
                                     per_carrier=config.frozen_mutants_per_carrier, seed=config.seed + 1000)
        self.qset = QualificationSet.build(blue, qual)
        # The round count is not frozen: no round depends on the total, so a
        # completed run may be extended with more rounds of the same config.
        config_body = {k: v for k, v in asdict(config).items() if k != "rounds"}
        frozen = {"qualification": sorted(c.buggy_hash for c in qual),
                  "splits": {k: sorted(c.carrier_id for c in v) for k, v in splits.items()},
                  "config": config_body, **self._extra_frozen()}
        checkpoint = self.state.checkpoint()
        stored = checkpoint.get("frozen_hash")
        frozen_hash = hash_payload(frozen)
        if stored and stored != frozen_hash:
            # checkpoints written before 2026-10-02 froze ``rounds`` too; accept
            # them when the stored run completed exactly that many rounds
            done = int(checkpoint.get("completed_rounds", 0))
            legacy = hash_payload({**frozen, "config": {**asdict(config), "rounds": done}})
            if stored != legacy:
                raise RuntimeError("resume refused: frozen sets or config differ from the checkpoint")
        self.frozen_hash = frozen_hash

    def _extra_frozen(self) -> dict[str, Any]:
        """Subclasses add settings that must not change across a resume."""
        return {}

    def _seed(self, *parts: Any) -> int:
        return int(hash_payload({"seed": self.config.seed, "parts": list(parts)})[7:15], 16)

    def run(self) -> dict[str, Any]:
        done = int(self.state.checkpoint().get("completed_rounds", 0))
        for round_index in range(done, self.config.rounds):
            self._round = round_index
            self.round(round_index)
            self.state.save_checkpoint({"completed_rounds": round_index + 1,
                                        "frozen_hash": self.frozen_hash})
        return {"completed_rounds": self.config.rounds, "rounds": self.state.read("rounds")}

    def round(self, r: int) -> dict[str, Any]:
        state, cfg = self.state, self.config
        discovery = self.splits["discovery"]
        admitted: list[Challenge] = []
        for i in range(cfg.proposals_per_round):
            carrier = discovery[(r * cfg.proposals_per_round + i) % len(discovery)]
            proposal = self.red.propose(carrier, state=state, seed=self._seed("red", r, i), round_index=r)
            state.append("red", proposal.record)
            if proposal.challenge is not None:
                admitted.append(proposal.challenge)

        solved = escalated = 0
        outcomes: dict[str, int] = {}
        for ch in admitted:
            pool = state.active_items()
            enc = self.blue.run(ch, mode="matched", pool=pool, inference=state.inference(),
                                matcher=state.matcher, seed=self._seed("blue", ch.challenge_id),
                                allow_escalation=True)
            if not enc.inconclusive:  # provider failures are not weak points
                state.record_episode(enc.episode, round_index=r)
                use = record_memory_usage(state, enc, round_index=r, pool=pool)
                outcomes[use["outcome"]] = outcomes.get(use["outcome"], 0) + 1
            state.append("encounters", {"round": r, "encounter": enc.record()})
            solved += enc.solved_within_budget
            escalated += enc.solved_by_escalation

        if not cfg.learning:
            summary = {"round": r, "proposals": cfg.proposals_per_round, "admitted": len(admitted),
                       "solved_within_budget": solved, "solved_by_escalation": escalated,
                       "candidates": 0, "decisions": [], "suspended": [], "learning": False,
                       "active": [[i.item_id, i.version, i.item_hash] for i in state.active_items()],
                       "episodes_total": len(state.read("episodes")), "cost": self.blue.budget.report()}
            state.append("rounds", summary)
            return summary
        authored = sorted(self.author.author(state.episodes()),
                          key=lambda i: (-i.payload["evidence"]["support"], i.item_id))
        candidates = []
        for item in authored:
            stored = _next_version(state, item)
            if stored is not None:
                state.store.add(stored, reason=f"authored in round {r}")
                candidates.append(stored)
            if len(candidates) >= cfg.max_candidates_per_round:
                break
        decisions = []
        baseline_cache: dict = {}
        for candidate in candidates:
            report = qualify_candidate(candidate, runner=self.blue, state=state, qset=self.qset,
                                       config=cfg.qualification, round_index=r,
                                       baseline_cache=baseline_cache)
            decisions.append({k: report[k] for k in ("item_id", "version", "decision", "reason",
                                                     "coverage", "helped", "harmed")})
            if report["decision"] == "active":
                for older in state.active_items():
                    if older.item_id == candidate.item_id and older.version < candidate.version:
                        state.store.transition(older.item_id, older.version, "retired",
                                               reason=f"superseded by v{candidate.version}")
        suspended = monitor_usage(state, cfg.monitor, round_index=r)
        summary = {
            "round": r,
            "proposals": cfg.proposals_per_round,
            "admitted": len(admitted),
            "solved_within_budget": solved,
            "solved_by_escalation": escalated,
            "memory_outcomes": outcomes,
            "candidates": len(candidates),
            "decisions": decisions,
            "suspended": suspended,
            "active": [[i.item_id, i.version, i.item_hash] for i in state.active_items()],
            "episodes_total": len(state.read("episodes")),
            "cost": self.blue.budget.report(),
        }
        state.append("rounds", summary)
        return summary
