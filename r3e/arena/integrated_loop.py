"""Deterministic macro-round orchestration for Arena and RAAM.

The promotion lane is frozen before a macro round starts.  A memory lane may
promote from episodes produced by the previous Arena round; the following
Arena challenge is then observation-only.  A policy lane skips memory
promotion and leaves the single promotion epoch to ``EvolutionRoundRunner``.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from r3e.arena.audit import verify_frozen_round
from r3e.arena.manifests import make_manifest, verify_manifest
from r3e.arena.renewed_challenge import (
    assert_renewed_challenge_binding,
)
from r3e.arena.runner import EvolutionRoundRunner
from r3e.memory.activation_guard import ActivationGuard
from r3e.memory.bank_store import ActiveBankStore
from r3e.memory.episode_store import EpisodeStore
from r3e.memory.memory_store import MemoryStore
from r3e.memory.retriever import MemoryRetriever
from r3e.memory.round_state import MemoryRoundState
from r3e.memory.runner import MemoryEvolutionRunner
from r3e.policy.registry_v2 import (
    get_active_policy,
    load_registry,
)
from r3e.protocol.events import EventLogger, detect_code_version
from r3e.protocol.hashing import (
    atomic_write_json,
    canonical_json,
    hash_payload,
    read_json,
    utc_now,
)
from r3e.protocol.ledger import read_ledger, writer_lock


INTEGRATED_CONFIG_SCHEMA = "r3e-integrated-coevolution-config-v1"
INTEGRATED_STATE_SCHEMA = "r3e-integrated-coevolution-state-v1"
INTEGRATED_AUDIT_SCHEMA = "r3e-integrated-coevolution-audit-v1"
PROMOTION_LANES = {"collect", "memory", "policy"}
MACRO_STAGES = (
    "INIT",
    "LOAD_PARENT",
    "MEMORY_EPOCH",
    "ARENA_EPOCH",
    "VERIFY_SINGLE_PROMOTION",
    "COMPLETE",
)


class IntegratedLoopViolation(RuntimeError):
    """Raised when a macro round cannot reconstruct a single promotion."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class IntegratedRoundState:
    def __init__(self, path: str | Path, *, round_id: str):
        self.path = Path(path)
        self.round_id = str(round_id)

    @staticmethod
    def _hash(payload: Mapping[str, Any]) -> str:
        return hash_payload({
            key: value
            for key, value in payload.items()
            if key not in {"state_hash", "updated_at"}
        })

    def initialize(self, config: Mapping[str, Any]) -> dict[str, Any]:
        if self.path.exists():
            value = self.load()
            if value["config_hash"] != hash_payload(config):
                raise IntegratedLoopViolation(
                    "integrated round config changed after initialization"
                )
            return value
        value = {
            "schema_version": INTEGRATED_STATE_SCHEMA,
            "round_id": self.round_id,
            "config_hash": hash_payload(config),
            "checkpoints": [],
            "updated_at": utc_now(),
        }
        value["state_hash"] = self._hash(value)
        atomic_write_json(self.path, value)
        return self.load()

    def load(self) -> dict[str, Any]:
        value = read_json(self.path)
        if (
            value.get("schema_version") != INTEGRATED_STATE_SCHEMA
            or value.get("round_id") != self.round_id
            or value.get("state_hash") != self._hash(value)
        ):
            raise IntegratedLoopViolation(
                "integrated round state is invalid"
            )
        return value

    def next_stage(self) -> str | None:
        index = len(self.load()["checkpoints"])
        return MACRO_STAGES[index] if index < len(MACRO_STAGES) else None

    def complete(self, stage: str, output: Any) -> None:
        value = self.load()
        expected = MACRO_STAGES[len(value["checkpoints"])]
        if stage != expected:
            raise IntegratedLoopViolation(
                f"expected macro stage {expected}, got {stage}"
            )
        value["checkpoints"].append({
            "stage": stage,
            "output_hash": hash_payload(output),
        })
        value["updated_at"] = utc_now()
        value["state_hash"] = self._hash(value)
        atomic_write_json(self.path, value)

    def verify(self, stage: str, output: Any) -> None:
        matches = [
            row for row in self.load()["checkpoints"]
            if row["stage"] == stage
        ]
        if (
            len(matches) != 1
            or matches[0]["output_hash"] != hash_payload(output)
        ):
            raise IntegratedLoopViolation(
                f"integrated artifact mismatch: {stage}"
            )


def verify_integrated_round(round_dir: str | Path) -> dict[str, Any]:
    """Reconstruct one macro round from frozen Arena and memory artifacts."""
    root = Path(round_dir)
    config = read_json(root / "macro_config.json")
    if config.get("schema_version") != INTEGRATED_CONFIG_SCHEMA:
        raise IntegratedLoopViolation("integrated config schema mismatch")
    lane = str(config.get("promotion_lane") or "")
    if lane not in PROMOTION_LANES:
        raise IntegratedLoopViolation("integrated promotion lane is invalid")
    state = IntegratedRoundState(
        root / "macro_state.json",
        round_id=str(config["round_id"]),
    )
    state.load()
    before = load_registry(root / "registry_before.json")
    memory_summary = read_json(root / "memory_epoch_summary.json")
    arena_reference = read_json(root / "arena_reference.json")
    arena_dir = Path(str(arena_reference["round_dir"]))
    arena_audit = verify_frozen_round(arena_dir)
    arena_summary = read_json(root / "arena_epoch_summary.json")
    after = load_registry(root / "registry_after.json")
    initial = get_active_policy(before)
    final = get_active_policy(after)
    memory_promoted = bool(memory_summary.get("promoted"))
    arena_promoted = bool(arena_summary.get("promoted"))
    if int(memory_promoted) + int(arena_promoted) > 1:
        raise IntegratedLoopViolation(
            "macro round performed more than one promotion"
        )
    if lane != "memory" and memory_promoted:
        raise IntegratedLoopViolation(
            "memory promoted outside its frozen lane"
        )
    if lane != "policy" and arena_promoted:
        raise IntegratedLoopViolation(
            "Arena promoted outside its frozen lane"
        )
    memory_active_hash = str(
        memory_summary.get("active_policy_hash")
        or initial.policy_hash
    )
    if arena_summary["parent_policy_hash"] != memory_active_hash:
        raise IntegratedLoopViolation(
            "Arena did not challenge the post-memory active policy"
        )
    if (
        arena_audit["parent_policy_hash"]
        != arena_summary["parent_policy_hash"]
        or arena_audit["active_policy_hash"]
        != arena_summary["active_policy_hash"]
        or arena_summary["active_policy_hash"] != final.policy_hash
    ):
        raise IntegratedLoopViolation(
            "Arena audit and macro registry disagree"
        )
    renewed = assert_renewed_challenge_binding(
        str(root / "registry_after.json"), final.policy_hash
    )
    persisted_renewed = read_json(
        arena_dir / "renewed_challenge_binding.json"
    )
    if persisted_renewed != renewed:
        raise IntegratedLoopViolation(
            "renewed challenge does not bind the macro-round final policy"
        )
    promoted = memory_promoted or arena_promoted
    if promoted and final.parent_policy_hash != initial.policy_hash:
        raise IntegratedLoopViolation(
            "macro promotion is not a direct child of its initial policy"
        )
    if not promoted and final.policy_hash != initial.policy_hash:
        raise IntegratedLoopViolation(
            "registry changed without a macro promotion"
        )
    record = {
        "schema_version": INTEGRATED_AUDIT_SCHEMA,
        "round_id": config["round_id"],
        "promotion_lane": lane,
        "config_hash": hash_payload(config),
        "registry_hash_before": before["registry_hash"],
        "registry_hash_after": after["registry_hash"],
        "parent_policy_hash": initial.policy_hash,
        "post_memory_policy_hash": memory_active_hash,
        "active_policy_hash": final.policy_hash,
        "memory_promoted": memory_promoted,
        "arena_promoted": arena_promoted,
        "promotion_count": int(memory_promoted) + int(arena_promoted),
        "memory_summary_hash": memory_summary["summary_hash"],
        "arena_summary_hash": hash_payload(arena_summary),
        "arena_audit_record_hash": arena_audit["audit_record_hash"],
        "verified_episode_manifest_hash": arena_audit[
            "verified_episode_manifest_hash"
        ],
        "renewed_challenge_hash": hash_payload(renewed),
    }
    record["audit_record_hash"] = hash_payload(record)
    return record


def freeze_integrated_audit(round_dir: str | Path) -> dict[str, Any]:
    root = Path(round_dir)
    target = root / "integrated_audit.json"
    rebuilt = verify_integrated_round(root)
    if target.exists():
        existing = read_json(target)
        if existing != rebuilt:
            raise IntegratedLoopViolation(
                "frozen integrated audit differs from reconstruction"
            )
        return existing
    atomic_write_json(target, rebuilt)
    return rebuilt


def _append_macro_ledger(
    path: Path,
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    with writer_lock(path.with_suffix(path.suffix + ".lock")):
        rows = read_ledger(path)
        matches = [
            row for row in rows
            if row.get("round_id") == audit.get("round_id")
        ]
        if matches:
            if (
                len(matches) != 1
                or matches[0].get("audit_record_hash")
                != audit.get("audit_record_hash")
            ):
                raise IntegratedLoopViolation(
                    "macro ledger contains a conflicting round"
                )
            return matches[0]
        row = {
            "operation": "integrated_round_complete",
            "round_id": audit["round_id"],
            "promotion_lane": audit["promotion_lane"],
            "promotion_count": audit["promotion_count"],
            "parent_policy_hash": audit["parent_policy_hash"],
            "active_policy_hash": audit["active_policy_hash"],
            "audit_record_hash": audit["audit_record_hash"],
            "ledger_index": len(rows),
            "previous_entry_hash": (
                rows[-1]["ledger_entry_hash"] if rows else ""
            ),
            "timestamp": utc_now(),
        }
        row["ledger_entry_hash"] = hash_payload(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return row


class IntegratedCoevolutionLoop:
    """Run resumable macro rounds with one pre-authorized promotion lane."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        project_root: str | Path,
        arena_adapter: Any,
        memory_adapter: Any,
        candidate_provider: Any | None = None,
        candidate_verifier: Any | None = None,
        semantic_signature_provider: Any | None = None,
        offline_allocator: Any | None = None,
    ):
        self.config = deepcopy(dict(config))
        if self.config.get("schema_version") != INTEGRATED_CONFIG_SCHEMA:
            raise IntegratedLoopViolation(
                "integrated loop config schema mismatch"
            )
        schedule = self.config.get("promotion_lane_schedule")
        if (
            not isinstance(schedule, list)
            or not schedule
            or any(str(lane) not in PROMOTION_LANES for lane in schedule)
        ):
            raise IntegratedLoopViolation(
                "promotion lane schedule is invalid"
            )
        if not isinstance(self.config.get("arena_config"), dict):
            raise IntegratedLoopViolation("arena_config is required")
        if not isinstance(self.config.get("memory_config"), dict):
            raise IntegratedLoopViolation("memory_config is required")
        self.root = Path(project_root).resolve()
        self.arena_adapter = arena_adapter
        self.memory_adapter = memory_adapter
        self.candidate_provider = candidate_provider
        self.candidate_verifier = candidate_verifier
        self.semantic_signature_provider = semantic_signature_provider
        self.offline_allocator = offline_allocator
        self.registry_path = self._path(
            self.config["policy_registry"]
        )
        self.memory_root = self._path(
            self.config.get("memory_root", "runtime/memory")
        )
        self.rounds_root = self._path(
            self.config.get(
                "integrated_rounds_root",
                "runtime/integrated/rounds",
            )
        )
        self.arena_rounds_root = self._path(
            self.config.get(
                "arena_rounds_root",
                "runtime/integrated/arena",
            )
        )
        self.events = EventLogger(
            self._path(
                self.config.get("events_root", "runtime/events")
            ),
            code_version=str(
                self.config.get("code_version")
                or detect_code_version(self.root)
            ),
        )
        self.events.ensure_streams()
        self.decision_ledger = self._path(
            self.config.get(
                "decision_ledger",
                "runtime/decision_ledger.jsonl",
            )
        )
        self.macro_ledger = self._path(
            self.config.get(
                "macro_ledger",
                "runtime/integrated/macro_ledger.jsonl",
            )
        )

    def _path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def _lane(self, index: int) -> str:
        schedule = self.config["promotion_lane_schedule"]
        return str(schedule[index % len(schedule)])

    def _non_target(self) -> dict[str, Any]:
        configured = self.config["arena_config"].get(
            "non_target_manifest"
        )
        if not configured:
            raise IntegratedLoopViolation(
                "integrated loop requires a non-target manifest"
            )
        return verify_manifest(read_json(self._path(configured)))

    def _memory_target(
        self,
        previous_round_id: str | None,
    ) -> tuple[dict[str, Any], str]:
        if previous_round_id:
            path = (
                self.arena_rounds_root
                / previous_round_id
                / "target_manifest.json"
            )
            if path.is_file():
                return verify_manifest(read_json(path)), str(path)
        return make_manifest([], split="target"), ""

    def _run_memory_epoch(
        self,
        *,
        round_id: str,
        round_dir: Path,
        lane: str,
        previous_round_id: str | None,
        initial_policy_hash: str,
    ) -> dict[str, Any]:
        target, target_path = self._memory_target(previous_round_id)
        if lane != "memory" or not target_path:
            summary = {
                "schema_version": "r3e-memory-epoch-skip-v1",
                "round_id": f"{round_id}_MEMORY",
                "parent_policy_hash": initial_policy_hash,
                "active_policy_hash": initial_policy_hash,
                "promoted": False,
                "qualified_memory_ids": [],
                "bank_hash": "",
                "promotion_decision_hash": "",
                "target_manifest_hash": target["manifest_hash"],
                "defer_reason": (
                    "lane_not_memory"
                    if lane != "memory"
                    else "no_prior_arena_target"
                ),
            }
            summary["summary_hash"] = hash_payload(summary)
            return summary
        episodes = EpisodeStore(self.memory_root / "episodes")
        memories = MemoryStore(
            self.memory_root / "library", episode_store=episodes
        )
        banks = ActiveBankStore(
            self.memory_root / "active_banks", memory_store=memories
        )
        retriever = MemoryRetriever(memories)
        guard = ActivationGuard(memories)
        memory_config = {
            **deepcopy(self.config["memory_config"]),
            "project_root": str(self.root),
            "code_version": self.events.code_version,
            "retriever_hash": retriever.retriever_hash,
            "activation_guard_hash": guard.guard_hash,
            "control_whitelist_hash": guard.control_whitelist_hash,
        }
        runner = MemoryEvolutionRunner(
            registry_path=self.registry_path,
            episode_store=episodes,
            memory_store=memories,
            bank_store=banks,
            target_manifest=target,
            non_target_manifest=self._non_target(),
            adapter=self.memory_adapter,
            round_id=f"{round_id}_MEMORY",
            work_dir=round_dir / "memory",
            config=memory_config,
            ledger_path=self.decision_ledger,
            event_logger=self.events,
        )
        summary = runner.run()
        if summary["parent_policy_hash"] != initial_policy_hash:
            raise IntegratedLoopViolation(
                "memory epoch loaded a stale macro parent"
            )
        return {
            **summary,
            "target_manifest_hash": target["manifest_hash"],
            "target_manifest_path": target_path,
            "summary_hash": hash_payload({
                **{
                    key: value
                    for key, value in summary.items()
                    if key != "summary_hash"
                },
                "target_manifest_hash": target["manifest_hash"],
                "target_manifest_path": target_path,
            }),
        }

    def run_round(
        self,
        *,
        round_id: str,
        round_index: int,
        previous_round_id: str | None = None,
    ) -> dict[str, Any]:
        lane = self._lane(round_index)
        round_dir = self.rounds_root / round_id
        round_dir.mkdir(parents=True, exist_ok=True)
        macro_config = {
            "schema_version": INTEGRATED_CONFIG_SCHEMA,
            "round_id": round_id,
            "round_index": int(round_index),
            "promotion_lane": lane,
            "policy_registry": str(self.registry_path),
            "memory_root": str(self.memory_root),
            "previous_round_id": previous_round_id or "",
            "source_config_hash": hash_payload(self.config),
        }
        state = IntegratedRoundState(
            round_dir / "macro_state.json", round_id=round_id
        )
        state.initialize(macro_config)
        if state.next_stage() == "INIT":
            atomic_write_json(round_dir / "macro_config.json", macro_config)
            state.complete("INIT", macro_config)
        if state.next_stage() == "LOAD_PARENT":
            registry_before = load_registry(self.registry_path)
            atomic_write_json(
                round_dir / "registry_before.json", registry_before
            )
            state.complete("LOAD_PARENT", registry_before)
        registry_before = load_registry(round_dir / "registry_before.json")
        parent = get_active_policy(registry_before)

        if state.next_stage() == "MEMORY_EPOCH":
            memory_summary = self._run_memory_epoch(
                round_id=round_id,
                round_dir=round_dir,
                lane=lane,
                previous_round_id=previous_round_id,
                initial_policy_hash=parent.policy_hash,
            )
            atomic_write_json(
                round_dir / "memory_epoch_summary.json",
                memory_summary,
            )
            state.complete("MEMORY_EPOCH", memory_summary)
        memory_summary = read_json(
            round_dir / "memory_epoch_summary.json"
        )

        if state.next_stage() == "ARENA_EPOCH":
            post_memory = get_active_policy(
                load_registry(self.registry_path)
            )
            if (
                bool(memory_summary.get("promoted"))
                != (post_memory.policy_hash != parent.policy_hash)
            ):
                raise IntegratedLoopViolation(
                    "memory promotion summary differs from registry"
                )
            arena_config = {
                **deepcopy(self.config["arena_config"]),
                "policy_registry": str(self.registry_path),
                "memory_root": str(self.memory_root),
                "rounds_root": str(self.arena_rounds_root),
                "events_root": str(self.events.root),
                "decision_ledger": str(self.decision_ledger),
                "expected_parent_policy_hash": post_memory.policy_hash,
                "policy_promotion_enabled": lane == "policy",
                "code_version": self.events.code_version,
            }
            arena = EvolutionRoundRunner(
                arena_config,
                round_id=round_id,
                adapter=self.arena_adapter,
                project_root=self.root,
                candidate_provider=self.candidate_provider,
                candidate_verifier=self.candidate_verifier,
                semantic_signature_provider=(
                    self.semantic_signature_provider
                ),
                offline_allocator=self.offline_allocator,
            )
            arena_summary = arena.run()
            atomic_write_json(
                round_dir / "arena_epoch_summary.json",
                arena_summary,
            )
            arena_reference = {
                "round_id": round_id,
                "round_dir": str(self.arena_rounds_root / round_id),
                "round_summary_hash": hash_payload(arena_summary),
            }
            arena_reference["reference_hash"] = hash_payload(
                arena_reference
            )
            atomic_write_json(
                round_dir / "arena_reference.json", arena_reference
            )
            state.complete("ARENA_EPOCH", arena_summary)
        arena_summary = read_json(round_dir / "arena_epoch_summary.json")

        if state.next_stage() == "VERIFY_SINGLE_PROMOTION":
            registry_after = load_registry(self.registry_path)
            atomic_write_json(
                round_dir / "registry_after.json", registry_after
            )
            audit = freeze_integrated_audit(round_dir)
            state.complete("VERIFY_SINGLE_PROMOTION", audit)
            _append_macro_ledger(self.macro_ledger, audit)
            self.events.emit(
                "arena",
                "integrated_round_completed",
                round_id=round_id,
                promotion_lane=lane,
                promotion_count=audit["promotion_count"],
                parent_policy_hash=audit["parent_policy_hash"],
                active_policy_hash=audit["active_policy_hash"],
                memory_summary_hash=audit["memory_summary_hash"],
                arena_audit_record_hash=(
                    audit["arena_audit_record_hash"]
                ),
                integrated_audit_record_hash=audit[
                    "audit_record_hash"
                ],
            )
        audit = read_json(round_dir / "integrated_audit.json")
        if state.next_stage() == "COMPLETE":
            summary = {
                "schema_version": (
                    "r3e-integrated-coevolution-summary-v1"
                ),
                "round_id": round_id,
                "promotion_lane": lane,
                "parent_policy_hash": audit["parent_policy_hash"],
                "post_memory_policy_hash": audit[
                    "post_memory_policy_hash"
                ],
                "active_policy_hash": audit["active_policy_hash"],
                "memory_promoted": audit["memory_promoted"],
                "arena_promoted": audit["arena_promoted"],
                "promotion_count": audit["promotion_count"],
                "integrated_audit_record_hash": audit[
                    "audit_record_hash"
                ],
                "arena_audit_record_hash": audit[
                    "arena_audit_record_hash"
                ],
                "verified_episode_manifest_hash": audit[
                    "verified_episode_manifest_hash"
                ],
                "renewed_challenge_hash": audit[
                    "renewed_challenge_hash"
                ],
            }
            summary["summary_hash"] = hash_payload(summary)
            atomic_write_json(round_dir / "summary.json", summary)
            state.complete("COMPLETE", summary)
        return read_json(round_dir / "summary.json")

    def run(self, round_ids: Iterable[str]) -> list[dict[str, Any]]:
        summaries = []
        previous = None
        for index, round_id in enumerate(round_ids):
            summaries.append(self.run_round(
                round_id=str(round_id),
                round_index=index,
                previous_round_id=previous,
            ))
            previous = str(round_id)
        return summaries
