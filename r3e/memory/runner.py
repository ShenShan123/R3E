"""Resumable RAAM qualification and whole-policy bank promotion runner."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

from r3e.arena.manifests import verify_manifest
from r3e.arena.paired_replay import paired_replay
from r3e.policy.promotion import (
    build_policy_promotion_bundle,
    decide_policy_promotion,
)
from r3e.policy.registry_v2 import (
    get_active_policy,
    load_registry,
    promote_policy,
    register_candidate,
    reject_policy,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.events import EventLogger, detect_code_version
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_jsonl,
    hash_payload,
    read_json,
)

from .bank_store import ActiveBankStore
from .authority import build_memory_promotion_authority
from .compatibility import classify_policy_compatibility
from .candidate_builder import build_memory_candidates
from .consolidator import select_bounded_active_memories
from .conformance import MemoryAdapterConformanceGate
from .episode_store import EpisodeStore
from .evidence import memory_definition
from .memory_store import MemoryStore
from .promotion import build_memory_bank_policy_candidate
from .qualification_gate import decide_memory_qualification
from .round_state import MemoryRoundState
from .schema import (
    ActiveMemoryBank,
    BudgetEnvelope,
    ControlMemory,
    MemoryLifecycleEvent,
    ShadowPairedResult,
)
from .shadow_replay import run_shadow_replay


class MemoryEvolutionViolation(RuntimeError):
    """Raised when a memory round cannot reconstruct its authority chain."""


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_jsonl(path, rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class MemoryEvolutionRunner:
    """Qualify stored experience without granting adapters registry authority."""

    def __init__(
        self,
        *,
        registry_path: str | Path,
        episode_store: EpisodeStore,
        memory_store: MemoryStore,
        bank_store: ActiveBankStore,
        target_manifest: dict[str, Any],
        non_target_manifest: dict[str, Any],
        adapter: Any,
        round_id: str,
        work_dir: str | Path,
        config: dict[str, Any] | None = None,
        ledger_path: str | Path | None = None,
        event_logger: EventLogger | None = None,
    ):
        self.registry_path = Path(registry_path)
        self.episode_store = episode_store
        self.memory_store = memory_store
        self.bank_store = bank_store
        self.target = verify_manifest(target_manifest)
        self.non_target = verify_manifest(non_target_manifest)
        if self.target["split"] != "target" or self.non_target["split"] != "non_target":
            raise MemoryEvolutionViolation("memory replay manifests have wrong split")
        self.adapter = adapter
        for method in ("replay_memory", "replay_policy"):
            if not callable(getattr(adapter, method, None)):
                raise MemoryEvolutionViolation(f"memory adapter missing {method}")
        self.adapter_gate = MemoryAdapterConformanceGate(adapter)
        self.round_id = round_id
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.config = {
            "minimum_support": 1,
            "maximum_active_memories": 3,
            "shadow_seeds": [101],
            "promotion_seeds": [201],
            "qualification_thresholds": None,
            "promotion_thresholds": None,
            "revalidate_memory_versions": {},
            "inherit_memory_versions": {},
            **(config or {}),
        }
        if self.config.get("inherit_memory_versions"):
            raise MemoryEvolutionViolation(
                "manual inherit_memory_versions is forbidden; inherit from the active bank"
            )
        self.state_config = {
            **self.config,
            "target_manifest_hash": self.target["manifest_hash"],
            "non_target_manifest_hash": self.non_target["manifest_hash"],
            "registry_path": str(self.registry_path.resolve()),
        }
        self.ledger_path = Path(ledger_path) if ledger_path else None
        code_version = str(
            self.config.get("code_version")
            or detect_code_version(Path(__file__).resolve().parents[2])
        )
        self.events = event_logger or EventLogger(
            self.work_dir / "events", code_version=code_version
        )
        self.state = MemoryRoundState(
            self.work_dir / "memory_round_state.json", round_id=round_id
        )

    def _artifact(self, name: str) -> Path:
        return self.work_dir / name

    def _transition(
        self,
        memory: ControlMemory,
        previous: str,
        new: str,
        policy_hash: str,
        evidence_hash: str,
    ) -> None:
        current = self.memory_store.current_status(
            memory.memory_id, memory.memory_version
        )
        if current == new:
            return
        if current != previous:
            raise MemoryEvolutionViolation(
                f"unexpected memory status: {memory.memory_id}: {current}"
            )
        self.memory_store.update_lifecycle(
            memory.memory_id,
            MemoryLifecycleEvent.create(
                memory_id=memory.memory_id,
                memory_version=memory.memory_version,
                memory_hash=memory.memory_hash,
                previous_status=previous,
                new_status=new,
                effective_policy_hash=policy_hash,
                reason_code=f"memory_round_{new}",
                evidence_hash=evidence_hash,
            ),
        )

    def _verify_completed(self) -> None:
        loaders = {
            "LOAD_PARENT": lambda: read_json(self._artifact("parent.json")),
            "BUILD_CANDIDATES": lambda: _read_jsonl(
                self._artifact("memory_candidates.jsonl")
            ),
            "SHADOW_QUALIFY": lambda: {
                "results": _read_jsonl(self._artifact("shadow_results.jsonl")),
                "decisions": _read_jsonl(
                    self._artifact("qualification_decisions.jsonl")
                ),
            },
            "BUILD_BANK_CHILD": lambda: {
                "bank": read_json(self._artifact("bank_candidate.json"))
                if self._artifact("bank_candidate.json").exists() else {},
                "child": read_json(self._artifact("policy_child.json"))
                if self._artifact("policy_child.json").exists() else {},
            },
            "PAIRED_REPLAY": lambda: _read_jsonl(
                self._artifact("policy_paired_replay.jsonl")
            ),
            "DECIDE": lambda: {
                "decision": read_json(
                    self._artifact("promotion_decision.json")
                ),
                "promotion_bundle": read_json(
                    self._artifact("promotion_bundle.json")
                ),
            },
            "ATOMIC_COMMIT": lambda: read_json(
                self._artifact("registry_after.json")
            )["registry_hash"],
            "COMPLETE": lambda: read_json(self._artifact("summary.json")),
        }
        completed = [row["stage"] for row in self.state.load()["checkpoints"]]
        for stage in completed:
            try:
                self.state.verify(stage, loaders[stage]())
            except (OSError, KeyError, ValueError) as exc:
                raise MemoryEvolutionViolation(
                    f"memory round resume artifact mismatch: {stage}: {exc}"
                ) from exc

    def run(self) -> dict[str, Any]:
        self.state.initialize(self.state_config)
        for name, manifest in (
            ("target_manifest.json", self.target),
            ("non_target_manifest.json", self.non_target),
        ):
            path = self._artifact(name)
            if path.exists():
                if read_json(path) != manifest:
                    raise MemoryEvolutionViolation(
                        f"frozen memory replay manifest changed: {name}"
                    )
            else:
                atomic_write_json(path, manifest)
        self._verify_completed()
        if self.state.next_stage() == "LOAD_PARENT":
            parent = get_active_policy(load_registry(self.registry_path))
            atomic_write_json(self._artifact("parent.json"), parent.to_dict())
            self.state.complete("LOAD_PARENT", parent.to_dict())
        parent = PolicyState.from_dict(read_json(self._artifact("parent.json")))
        if get_active_policy(load_registry(self.registry_path)).policy_hash not in {
            parent.policy_hash,
            str(
                (
                    read_json(self._artifact("policy_child.json"))
                    if self._artifact("policy_child.json").exists()
                    else {}
                ).get("policy_hash") or ""
            ),
        }:
            raise MemoryEvolutionViolation("memory round parent became stale")

        if self.state.next_stage() == "BUILD_CANDIDATES":
            episodes = [
                episode for episode in self.episode_store.audit()
                if episode.challenged_effective_policy_hash
                == parent.effective_policy_hash
            ]
            candidates = build_memory_candidates(
                episodes,
                policy=parent,
                origin_round_id=self.round_id,
                minimum_support=int(self.config["minimum_support"]),
            )
            accepted = []
            for candidate in candidates:
                resolved = self.memory_store.resolve_candidate(candidate)
                if all(
                    existing.memory_hash != resolved.memory_hash
                    for existing in accepted
                ):
                    accepted.append(resolved)
            known = {
                (memory.memory_id, memory.memory_version)
                for memory in accepted
            }
            for memory_id, version in sorted(
                self.config["revalidate_memory_versions"].items()
            ):
                memory = self.memory_store.get_version(
                    memory_id, int(version)
                )
                if (memory.memory_id, memory.memory_version) not in known:
                    accepted.append(memory)
                    known.add((memory.memory_id, memory.memory_version))
            rows = [candidate.to_dict() for candidate in accepted]
            _write_jsonl(self._artifact("memory_candidates.jsonl"), rows)
            self.state.complete("BUILD_CANDIDATES", rows)
        candidates = [
            ControlMemory.from_dict(row)
            for row in _read_jsonl(self._artifact("memory_candidates.jsonl"))
        ]

        budget = BudgetEnvelope(
            max_llm_calls=int(parent.budgets["max_llm_calls_per_case"]),
            max_verifier_calls=(
                int(parent.budgets["max_llm_calls_per_case"])
                * len(parent.configuration["verifier_order"])
            ),
            max_tokens=int(parent.budgets["max_tokens_per_case"]),
            max_wall_seconds=float(parent.budgets["max_wall_seconds_per_case"]),
        )
        if self.state.next_stage() == "SHADOW_QUALIFY":
            result_rows = []
            decisions = []
            for memory in candidates:
                def replay_case(row, split):
                    value = dict(row)
                    if "trigger_matched" not in value:
                        observable = {
                            "oracle_stage": "functional_compare",
                            "sequential_context": int(
                                value.get("sequential_depth") or 0
                            ) > 0,
                            "affected_roles": [
                                str(value.get("affected_role") or "unknown")
                            ],
                            "mismatch_pattern": str(
                                value.get("effect") or "unknown"
                            ),
                            "first_divergence_bucket": str(
                                value.get("first_divergence_cycle_bucket")
                                or "unknown"
                            ),
                        }
                        value["trigger_matched"] = all(
                            observable.get(key) == expected
                            for key, expected in memory.trigger_predicate.items()
                        )
                    value["memory_replay_split"] = split
                    return value

                cases = [
                    replay_case(row, "target") for row in self.target["rows"]
                ] + [
                    replay_case(row, "non_target")
                    for row in self.non_target["rows"]
                ]
                status = self.memory_store.current_status(
                    memory.memory_id, memory.memory_version
                )
                is_revalidation = (
                    status == "revalidation_required"
                    or memory.created_under_effective_policy_hash
                    != parent.effective_policy_hash
                )
                if status == "candidate":
                    self._transition(
                        memory, "candidate", "shadow_testing",
                        parent.effective_policy_hash, memory.memory_hash,
                    )
                elif status == "revalidation_required":
                    self._transition(
                        memory, "revalidation_required", "shadow_testing",
                        parent.effective_policy_hash, memory.memory_hash,
                    )
                elif status not in {
                    "shadow_testing", "replay_qualified", "active_dormant",
                    "harmful",
                }:
                    raise MemoryEvolutionViolation(
                        f"memory cannot enter shadow replay from {status}"
                    )
                current_evidence_set = self.memory_store.evidence_set(
                    memory.memory_id, memory.memory_version
                )
                qualification_path = (
                    self.work_dir
                    / "qualification"
                    / (
                        f"{memory.memory_hash.replace(':', '_')}_"
                        f"{current_evidence_set['evidence_set_hash'].replace(':', '_')}.json"
                    )
                )
                if qualification_path.exists():
                    frozen = read_json(qualification_path)
                    if frozen.get("memory_hash") != memory.memory_hash:
                        raise MemoryEvolutionViolation(
                            "qualification cache memory hash mismatch"
                        )
                    results = [
                        ShadowPairedResult(**row)
                        for row in frozen["results"]
                    ]
                    decision = frozen["decision"]
                else:
                    results = [
                        run_shadow_replay(
                            case=case,
                            policy=parent,
                            memory=memory,
                            seed=int(seed),
                            budget=budget,
                            evaluator=lambda replay_policy, replay_case, replay_seed, plan, envelope: (
                                self.adapter_gate.validate_shadow(
                                    self.adapter.replay_memory(
                                        replay_policy,
                                        replay_case,
                                        replay_seed,
                                        plan,
                                        envelope,
                                    ),
                                    policy=replay_policy,
                                    case=replay_case,
                                    seed=replay_seed,
                                    execution_plan=plan,
                                    budget=envelope,
                                )
                            ),
                        )
                        for case in cases
                        for seed in self.config["shadow_seeds"]
                    ]
                    decision = decide_memory_qualification(
                        memory,
                        results,
                        thresholds=self.config.get("qualification_thresholds"),
                        evidence_set=current_evidence_set,
                        provenance={
                            "manifest_hash": hash_payload({
                                "target": self.target["manifest_hash"],
                                "non_target": self.non_target["manifest_hash"],
                            }),
                            "toolchain_fingerprint_hash": hash_payload(
                                getattr(self.adapter, "toolchain_fingerprint", {})
                            ),
                            "code_commit_sha": self.events.code_version,
                            "control_whitelist_hash": self.config[
                                "control_whitelist_hash"
                            ],
                            "policy_instance_hash": parent.policy_instance_hash,
                            "effective_policy_hash": parent.effective_policy_hash,
                            **(
                                {
                                    "revalidation_from_effective_policy_hash": (
                                        memory.created_under_effective_policy_hash
                                    )
                                }
                                if memory.created_under_effective_policy_hash
                                != parent.effective_policy_hash
                                else {}
                            ),
                        },
                    )
                    qualification_bundle = {
                        "schema_version": "r3e-memory-qualification-bundle-v1",
                        "memory_hash": memory.memory_hash,
                        "evidence_set": current_evidence_set,
                        "results": [row.to_dict() for row in results],
                        "decision": decision,
                    }
                    atomic_write_json(qualification_path, qualification_bundle)
                    self.memory_store.store_qualification_bundle(
                        qualification_bundle
                    )
                if decision["qualified"]:
                    status = self.memory_store.current_status(
                        memory.memory_id, memory.memory_version
                    )
                    if status == "shadow_testing":
                        self._transition(
                            memory, "shadow_testing", "replay_qualified",
                            parent.effective_policy_hash,
                            decision["decision_hash"],
                        )
                        status = "replay_qualified"
                    if status not in {"replay_qualified", "active_dormant"}:
                        raise MemoryEvolutionViolation(
                            f"qualified memory has incompatible status: {status}"
                        )
                elif decision["summary"]["harmed"] > 0:
                    status = self.memory_store.current_status(
                        memory.memory_id, memory.memory_version
                    )
                    if status == "shadow_testing":
                        self._transition(
                            memory, "shadow_testing", "harmful",
                            parent.effective_policy_hash,
                            decision["decision_hash"],
                        )
                    elif status != "harmful":
                        raise MemoryEvolutionViolation(
                            f"harmful memory has incompatible status: {status}"
                        )
                else:
                    status = self.memory_store.current_status(
                        memory.memory_id, memory.memory_version
                    )
                    if status == "shadow_testing":
                        self._transition(
                            memory,
                            "shadow_testing",
                            "stale" if is_revalidation else "candidate",
                            parent.effective_policy_hash,
                            decision["decision_hash"],
                        )
                    elif status != (
                        "stale" if is_revalidation else "candidate"
                    ):
                        raise MemoryEvolutionViolation(
                            f"unqualified memory has incompatible status: {status}"
                        )
                result_rows.extend([
                    {"memory_id": memory.memory_id, **row.to_dict()}
                    for row in results
                ])
                decisions.append(decision)
            output = {"results": result_rows, "decisions": decisions}
            _write_jsonl(self._artifact("shadow_results.jsonl"), result_rows)
            _write_jsonl(
                self._artifact("qualification_decisions.jsonl"), decisions
            )
            self.state.complete("SHADOW_QUALIFY", output)
        decisions = _read_jsonl(
            self._artifact("qualification_decisions.jsonl")
        )

        if self.state.next_stage() == "BUILD_BANK_CHILD":
            by_id = {memory.memory_id: memory for memory in candidates}
            selected = select_bounded_active_memories(
                [
                    (by_id[decision["memory_id"]], decision)
                    for decision in decisions
                    if decision["memory_id"] in by_id
                ],
                maximum_active=int(self.config["maximum_active_memories"]),
            )
            inherited = {}
            if parent.memory_binding:
                inherited = {
                    memory_id: int(binding["memory_version"])
                    for memory_id, binding in self.bank_store.load_for_policy(
                        parent
                    ).memories.items()
                }
            for memory_id, version in inherited.items():
                if self.memory_store.current_status(
                    memory_id, version
                ) != "active_dormant":
                    raise MemoryEvolutionViolation(
                        "inherited memory is not active_dormant"
                    )
            ordered_selected = {
                **inherited,
                **{
                    memory_id: version
                    for memory_id, version in selected.items()
                    if memory_id not in inherited
                },
            }
            selected = dict(
                list(ordered_selected.items())[
                    : int(self.config["maximum_active_memories"])
                ]
            )
            definition_seen = set()
            semantic_selected = {}
            for memory_id, version in selected.items():
                memory = self.memory_store.get_version(memory_id, version)
                definition_hash = memory_definition(memory)["definition_hash"]
                if definition_hash in definition_seen:
                    continue
                definition_seen.add(definition_hash)
                semantic_selected[memory_id] = version
            selected = semantic_selected
            if selected:
                for memory_id, version in selected.items():
                    memory = self.memory_store.get_version(memory_id, version)
                    if self.memory_store.current_status(memory_id, version) == "replay_qualified":
                        qualification = next(
                            item for item in decisions
                            if item["memory_id"] == memory_id
                            and int(item["memory_version"]) == version
                        )
                        self._transition(
                            memory,
                            "replay_qualified",
                            "bank_candidate",
                            parent.effective_policy_hash,
                            qualification["decision_hash"],
                        )
                source_bindings = []
                for memory_id, version in selected.items():
                    memory = self.memory_store.get_version(memory_id, version)
                    evidence_set = self.memory_store.evidence_set(
                        memory_id, version
                    )
                    current_decision = next(
                        (
                            item for item in decisions
                            if item["memory_id"] == memory_id
                            and item.get("evidence_set_hash")
                            == evidence_set["evidence_set_hash"]
                        ),
                        None,
                    )
                    if current_decision is None:
                        current_decision = self.memory_store.get_qualification_bundle(
                            memory.memory_hash
                        )["decision"]
                    source_bindings.append({
                        "memory_definition_hash": memory_definition(memory)[
                            "definition_hash"
                        ],
                        "evidence_set_hash": evidence_set[
                            "evidence_set_hash"
                        ],
                        "qualification_decision_hash": current_decision[
                            "decision_hash"
                        ],
                    })
                source_manifest_hash = hash_payload({
                    "memory_authority_bindings": sorted(
                        source_bindings,
                        key=lambda row: row["memory_definition_hash"],
                    )
                })
                child, bank = build_memory_bank_policy_candidate(
                    parent=parent,
                    store=self.memory_store,
                    memory_versions=selected,
                    bank_id=f"AMB_{self.round_id}",
                    bank_version=1,
                    retriever_hash=self.config["retriever_hash"],
                    activation_guard_hash=self.config["activation_guard_hash"],
                    control_whitelist_hash=self.config[
                        "control_whitelist_hash"
                    ],
                    round_id=self.round_id,
                    policy_id=f"{parent.policy_id}_{self.round_id}_M01",
                    source_manifest_hash=source_manifest_hash,
                )
                if child.effective_policy_hash == parent.effective_policy_hash:
                    for memory_id, version in selected.items():
                        memory = self.memory_store.get_version(
                            memory_id, version
                        )
                        if self.memory_store.current_status(
                            memory_id, version
                        ) == "bank_candidate":
                            self._transition(
                                memory,
                                "bank_candidate",
                                "replay_qualified",
                                parent.effective_policy_hash,
                                source_manifest_hash,
                            )
                    output = {"bank": {}, "child": {}}
                else:
                    self.bank_store.add_candidate(bank)
                    registry = load_registry(self.registry_path)
                    if child.policy_id not in registry["policies"]:
                        register_candidate(
                            self.registry_path,
                            child,
                            ledger_path=self.ledger_path,
                            event_logger=self.events,
                            round_id=self.round_id,
                        )
                    atomic_write_json(
                        self._artifact("bank_candidate.json"), bank.to_dict()
                    )
                    atomic_write_json(
                        self._artifact("policy_child.json"), child.to_dict()
                    )
                    output = {"bank": bank.to_dict(), "child": child.to_dict()}
            else:
                output = {"bank": {}, "child": {}}
            self.state.complete("BUILD_BANK_CHILD", output)
        child_raw = (
            read_json(self._artifact("policy_child.json"))
            if self._artifact("policy_child.json").exists() else {}
        )

        if self.state.next_stage() == "PAIRED_REPLAY":
            if child_raw:
                child = PolicyState.from_dict(child_raw)
                bank = self.bank_store.get_by_hash(
                    child.memory_binding["active_memory_bank_hash"]
                )

                def replay(policy, case, seed):
                    replay_bank = (
                        bank if policy.policy_hash == child.policy_hash else None
                    )
                    return self.adapter_gate.validate_policy_replay(
                        self.adapter.replay_policy(
                            policy, replay_bank, case, seed,
                        ),
                        policy=policy,
                        bank=replay_bank,
                        case=case,
                        seed=seed,
                    )

                replay_rows = paired_replay(
                    parent,
                    child,
                    target_manifest=self.target,
                    non_target_manifest=self.non_target,
                    seeds=[int(seed) for seed in self.config["promotion_seeds"]],
                    evaluator=replay,
                )
            else:
                replay_rows = []
            _write_jsonl(
                self._artifact("policy_paired_replay.jsonl"), replay_rows
            )
            self.state.complete("PAIRED_REPLAY", replay_rows)
        replay_rows = _read_jsonl(
            self._artifact("policy_paired_replay.jsonl")
        )

        if self.state.next_stage() == "DECIDE":
            if child_raw:
                child = PolicyState.from_dict(child_raw)
                bank = ActiveMemoryBank.from_dict(
                    read_json(self._artifact("bank_candidate.json"))
                )
                provenance = {
                    "round_id": self.round_id,
                    "residual_manifest_hash": child.created_from_residual_manifest_hash,
                    "adaptation_manifest_hash": hash_payload({
                        "qualification_decisions": [
                            decision["decision_hash"] for decision in decisions
                        ],
                    }),
                    "target_manifest_hash": self.target["manifest_hash"],
                    "non_target_manifest_hash": self.non_target["manifest_hash"],
                    "paired_result_hash": hash_payload(replay_rows),
                    "code_commit_sha": self.events.code_version,
                    "toolchain_fingerprint_hash": hash_payload(
                        getattr(self.adapter, "toolchain_fingerprint", {})
                    ),
                    "run_context_hash": hash_payload({
                        "parent_policy_hash": parent.policy_hash,
                        "round_id": self.round_id,
                        "config": self.config,
                    }),
                    "toolchain_fingerprint": dict(
                        getattr(self.adapter, "toolchain_fingerprint", {})
                    ),
                }
                decision = decide_policy_promotion(
                    parent,
                    child,
                    replay_rows,
                    validation_manifest_hash=hash_payload({
                        "target": self.target["manifest_hash"],
                        "non_target": self.non_target["manifest_hash"],
                    }),
                    thresholds=self.config.get("promotion_thresholds"),
                    provenance=provenance,
                )
                authority_memories = [
                    self.memory_store.get_version(
                        memory_id, int(binding["memory_version"])
                    )
                    for memory_id, binding in bank.memories.items()
                ]
                qualification_bundles = []
                compatibility_bundles = []
                authority_episodes = {}
                registry = load_registry(self.registry_path)
                policies_by_hash = {
                    entry["policy_hash"]: PolicyState.from_dict(entry["policy"])
                    for entry in registry["policies"].values()
                }
                for memory in authority_memories:
                    local_matches = sorted(
                        (self.work_dir / "qualification").glob(
                            f"{memory.memory_hash.replace(':', '_')}_*.json"
                        )
                    )
                    qualification = (
                        read_json(local_matches[-1])
                        if local_matches
                        else self.memory_store.get_qualification_bundle(
                            memory.memory_hash
                        )
                    )
                    qualification_bundles.append(qualification)
                    for link in qualification["evidence_set"]["links"]:
                        episode = self.episode_store.get(link["episode_id"])
                        authority_episodes[episode.episode_id] = episode
                    qualified_under_instance = qualification["decision"][
                        "qualified_under_policy_instance_hash"
                    ]
                    qualified_under_effective = qualification["decision"][
                        "qualified_under_effective_policy_hash"
                    ]
                    if qualified_under_effective != parent.effective_policy_hash:
                        source_policy = policies_by_hash.get(
                            qualified_under_instance
                        )
                        if source_policy is None:
                            raise MemoryEvolutionViolation(
                                "inherited memory qualification policy is unavailable"
                            )
                        compatibility = {
                            "memory_hash": memory.memory_hash,
                            "source_policy": source_policy.to_dict(),
                            "decision": classify_policy_compatibility(
                                memory, source_policy, parent
                            ),
                        }
                        compatibility["decision_hash"] = hash_payload(
                            compatibility
                        )
                        compatibility_bundles.append(compatibility)
                memory_authority = build_memory_promotion_authority(
                    bank=bank,
                    memories=authority_memories,
                    qualification_bundles=qualification_bundles,
                    episodes=list(authority_episodes.values()),
                    compatibility_bundles=compatibility_bundles,
                )
                promotion_bundle = build_policy_promotion_bundle(
                    parent,
                    child,
                    replay_rows,
                    validation_manifest_hash=hash_payload({
                        "target": self.target["manifest_hash"],
                        "non_target": self.non_target["manifest_hash"],
                    }),
                    target_manifest=self.target,
                    non_target_manifest=self.non_target,
                    thresholds=self.config.get("promotion_thresholds"),
                    provenance=provenance,
                    recorded_decision=decision,
                    memory_authority=memory_authority,
                )
            else:
                decision = {
                    "schema_version": "r3e-memory-no-candidate-v1",
                    "promote": False,
                    "decision": "no_candidate",
                }
                decision["decision_hash"] = hash_payload(decision)
                promotion_bundle = {}
            atomic_write_json(
                self._artifact("promotion_decision.json"), decision
            )
            atomic_write_json(
                self._artifact("promotion_bundle.json"), promotion_bundle
            )
            self.state.complete(
                "DECIDE",
                {"decision": decision, "promotion_bundle": promotion_bundle},
            )
        decision = read_json(self._artifact("promotion_decision.json"))
        promotion_bundle = read_json(self._artifact("promotion_bundle.json"))

        if self.state.next_stage() == "ATOMIC_COMMIT":
            registry = load_registry(self.registry_path)
            if child_raw and decision.get("promote"):
                child = PolicyState.from_dict(child_raw)
                active = get_active_policy(registry)
                if active.policy_hash == child.policy_hash:
                    registry_after = registry
                elif active.policy_hash == parent.policy_hash:
                    registry_after = promote_policy(
                        self.registry_path,
                        child.policy_id,
                        promotion_bundle,
                        ledger_path=self.ledger_path,
                        event_logger=self.events,
                        round_id=self.round_id,
                    )
                else:
                    raise MemoryEvolutionViolation("memory child became stale")
                for memory_id, binding in ActiveMemoryBank.from_dict(
                    read_json(self._artifact("bank_candidate.json"))
                ).memories.items():
                    memory = self.memory_store.get_version(
                        memory_id, int(binding["memory_version"])
                    )
                    if self.memory_store.current_status(
                        memory_id, memory.memory_version
                    ) == "bank_candidate":
                        self._transition(
                            memory,
                            "bank_candidate",
                            "active_dormant",
                            child.effective_policy_hash,
                            promotion_bundle["bundle_hash"],
                        )
            else:
                registry_after = registry
                if child_raw:
                    child = PolicyState.from_dict(child_raw)
                    if (
                        registry_after["policies"][child.policy_id]["status"]
                        == "candidate"
                    ):
                        registry_after = reject_policy(
                            self.registry_path,
                            child.policy_id,
                            decision.get("rejection_reasons")
                            or ["memory_qualification_or_replay_failed"],
                            ledger_path=self.ledger_path,
                            event_logger=self.events,
                            round_id=self.round_id,
                        )
                    bank = ActiveMemoryBank.from_dict(
                        read_json(self._artifact("bank_candidate.json"))
                    )
                    for memory_id, binding in bank.memories.items():
                        memory = self.memory_store.get_version(
                            memory_id, int(binding["memory_version"])
                        )
                        if self.memory_store.current_status(
                            memory_id, memory.memory_version
                        ) == "bank_candidate":
                            self._transition(
                                memory,
                                "bank_candidate",
                                "replay_qualified",
                                parent.effective_policy_hash,
                                decision["decision_hash"],
                            )
            atomic_write_json(
                self._artifact("registry_after.json"), registry_after
            )
            self.state.complete(
                "ATOMIC_COMMIT", registry_after["registry_hash"]
            )

        if self.state.next_stage() == "COMPLETE":
            active = get_active_policy(load_registry(self.registry_path))
            summary = {
                "schema_version": "r3e-memory-round-summary-v1",
                "round_id": self.round_id,
                "parent_policy_hash": parent.policy_hash,
                "active_policy_hash": active.policy_hash,
                "promoted": active.policy_hash != parent.policy_hash,
                "qualified_memory_ids": [
                    decision["memory_id"]
                    for decision in decisions if decision.get("qualified")
                ],
                "bank_hash": (
                    (active.memory_binding or {}).get(
                        "active_memory_bank_hash", ""
                    )
                ),
                "promotion_decision_hash": decision["decision_hash"],
            }
            summary["summary_hash"] = hash_payload(summary)
            atomic_write_json(self._artifact("summary.json"), summary)
            self.state.complete("COMPLETE", summary)
        return read_json(self._artifact("summary.json"))


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one resumable RAAM qualification/promotion round"
    )
    parser.add_argument("--registry", required=True)
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--target-manifest", required=True)
    parser.add_argument("--non-target-manifest", required=True)
    parser.add_argument("--adapter", required=True, help="module:factory")
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--ledger")
    args = parser.parse_args()
    module_name, factory_name = args.adapter.split(":", 1)
    adapter = getattr(importlib.import_module(module_name), factory_name)()
    memory_root = Path(args.memory_root)
    episodes = EpisodeStore(memory_root / "episodes")
    memories = MemoryStore(
        memory_root / "library", episode_store=episodes
    )
    banks = ActiveBankStore(
        memory_root / "active_banks", memory_store=memories
    )
    summary = MemoryEvolutionRunner(
        registry_path=args.registry,
        episode_store=episodes,
        memory_store=memories,
        bank_store=banks,
        target_manifest=read_json(args.target_manifest),
        non_target_manifest=read_json(args.non_target_manifest),
        adapter=adapter,
        round_id=args.round_id,
        work_dir=args.work_dir,
        config=read_json(args.config),
        ledger_path=args.ledger,
    ).run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
