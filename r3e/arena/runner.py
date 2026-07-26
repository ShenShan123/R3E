"""Resumable whole-policy evolution round runner.

Tool- and model-specific behavior is supplied by a frozen adapter object. The
runner owns authority, manifests, hashing, state transitions, and registry
mutation; adapters only generate/evaluate individual cases.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

from r3e.policy.promotion import decide_policy_promotion, select_single_promotable_child
from r3e.policy.registry_v2 import (
    get_active_policy,
    load_registry,
    promote_policy,
    register_candidate,
    reject_policy,
)
from r3e.policy.search import propose_children
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import atomic_write_json, canonical_json, hash_payload, read_json
from r3e.protocol.events import EventLogger, detect_code_version
from r3e.red.archive import load_archive, update_archive
from r3e.red.challenge import evaluate_challenge
from r3e.red.novelty import archive_cell, novelty_score
from r3e.red.validity import validity_gate

from .manifests import freeze_manifest, grouped_split, make_manifest, verify_manifest
from .paired_replay import paired_replay
from .renewed_challenge import assert_renewed_challenge_binding
from .round_state import RoundState, RoundStateViolation


class RoundRunnerViolation(RuntimeError):
    """Raised when the round config or adapter violates protocol boundaries."""


class _SplitEvolutionAdapter:
    """Bind separately supplied red/blue adapters to the runner protocol."""

    def __init__(self, red_adapter: Any, blue_adapter: Any):
        self.red_adapter = red_adapter
        self.blue_adapter = blue_adapter
        self.toolchain_fingerprint = {
            "red": dict(getattr(red_adapter, "toolchain_fingerprint", {}) or {}),
            "blue": dict(getattr(blue_adapter, "toolchain_fingerprint", {}) or {}),
        }

    def generate_red(self, parent: PolicyState, config: dict[str, Any]):
        return self.red_adapter.generate_red(parent, config)

    def prepare_validity(self, poison: dict[str, Any]):
        return self.red_adapter.prepare_validity(poison)

    def evaluate_blue(self, policy: PolicyState, poison: dict[str, Any], seed: int):
        return self.blue_adapter.evaluate_blue(policy, poison, seed)

    def probe_learnability(self, policy: PolicyState, poison: dict[str, Any]):
        method = getattr(self.red_adapter, "probe_learnability", None)
        if not callable(method):
            method = self.blue_adapter.probe_learnability
        return method(policy, poison)

    def screen_child(
        self,
        parent: PolicyState,
        child: PolicyState,
        adaptation_manifest: dict[str, Any],
    ):
        return self.blue_adapter.screen_child(parent, child, adaptation_manifest)

    def replay(self, policy: PolicyState, case: dict[str, Any], seed: int):
        return self.blue_adapter.replay(policy, case, seed)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical_json(row) + "\n")
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_adapter(spec: str, config: dict[str, Any]):
    if ":" not in spec:
        raise RoundRunnerViolation("adapter must use module:factory syntax")
    module_name, factory_name = spec.split(":", 1)
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory(config)


class EvolutionRoundRunner:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        round_id: str,
        adapter: Any,
        project_root: str | Path,
    ):
        self.config = config
        self.round_id = round_id
        self.adapter = adapter
        required_methods = {
            "generate_red",
            "prepare_validity",
            "evaluate_blue",
            "probe_learnability",
            "screen_child",
            "replay",
        }
        missing = sorted(
            name for name in required_methods if not callable(getattr(adapter, name, None))
        )
        if missing:
            raise RoundRunnerViolation(f"evolution adapter missing methods: {missing}")
        self.root = Path(project_root)
        self.round_dir = self.root / config.get("rounds_root", "runtime/rounds") / round_id
        self.round_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root / config["policy_registry"]
        self.ledger_path = self.root / config.get(
            "decision_ledger", "runtime/registry/decision_ledger.jsonl"
        )
        self.archive_path = self.root / config.get(
            "red_archive", "runtime/archives/red_residual_archive.jsonl"
        )
        self.events = EventLogger(
            self.root / config.get("events_root", "runtime/events"),
            code_version=str(config.get("code_version") or detect_code_version(self.root)),
        )
        self.events.ensure_streams()
        self.state = RoundState(self.round_dir / "round_state.json", round_id=round_id)

    def _checkpoint(self, stage: str, stage_input: Any, stage_output: Any) -> None:
        self.state.complete_stage(stage, stage_input=stage_input, stage_output=stage_output)
        self.events.emit(
            "arena",
            "round_stage_completed",
            round_id=self.round_id,
            stage=stage,
            stage_input_hash=hash_payload(stage_input),
            stage_output_hash=hash_payload(stage_output),
        )

    def _verify_persisted_checkpoints(self) -> None:
        """Fail closed when any completed stage artifact was changed."""
        state = self.state.load()
        completed = {row["stage"] for row in state["checkpoints"]}
        loaders = {
            "INIT": lambda: {"round_dir": str(self.round_dir)},
            "LOAD_ACTIVE_POLICY": lambda: read_json(self.round_dir / "active_parent.json"),
            "RED_GENERATE": lambda: _read_jsonl(self.round_dir / "red_candidates.jsonl"),
            "VALIDITY_GATE": lambda: _read_jsonl(self.round_dir / "validity_results.jsonl"),
            "BLUE_CHALLENGE": lambda: _read_jsonl(
                self.round_dir / "blue_challenge_results.jsonl"
            ),
            "ARCHIVE_UPDATE": lambda: _read_jsonl(
                self.round_dir / "archive_updates.jsonl"
            ),
            "FREEZE_RESIDUAL_MANIFEST": lambda: read_json(
                self.round_dir / "residual_manifest.json"
            ),
            "SPLIT_ADAPT_TARGET": lambda: {
                "adaptation": read_json(
                    self.round_dir / "adaptation_manifest.json"
                )["manifest_hash"],
                "target": read_json(
                    self.round_dir / "target_manifest.json"
                )["manifest_hash"],
            },
            "PROPOSE_CHILDREN": lambda: [
                PolicyState.from_dict(row).policy_hash
                for row in _read_jsonl(self.round_dir / "child_policies.jsonl")
            ],
            "SCREEN_CHILDREN": lambda: _read_jsonl(
                self.round_dir / "screening_results.jsonl"
            ),
            "FREEZE_PROMOTION_MANIFEST": lambda: read_json(
                self.round_dir / "promotion_manifest.json"
            ),
            "PAIRED_REPLAY": lambda: _read_jsonl(
                self.round_dir / "paired_validation.jsonl"
            ),
            "DECIDE": lambda: _read_jsonl(
                self.round_dir / "promotion_decisions.jsonl"
            ),
            "ATOMIC_COMMIT": lambda: read_json(
                self.round_dir / "registry_after.json"
            )["registry_hash"],
            "RENEWED_CHALLENGE": lambda: read_json(
                self.round_dir / "renewed_challenge_binding.json"
            ),
            "COMPLETE": lambda: read_json(self.round_dir / "round_summary.json"),
        }
        for stage in completed:
            loader = loaders.get(stage)
            if loader is None:
                raise RoundRunnerViolation(f"no artifact verifier for stage: {stage}")
            try:
                output = loader()
                self.state.verify_stage(stage, stage_output=output)
            except (OSError, KeyError, ValueError, RoundStateViolation) as exc:
                raise RoundRunnerViolation(
                    f"persisted stage artifact mismatch: {stage}: {exc}"
                ) from exc

    def run(self) -> dict[str, Any]:
        state = self.state.initialize(self.config)
        if state["round_config_hash"] != hash_payload(self.config):
            raise RoundRunnerViolation("round config changed after initialization")
        self._verify_persisted_checkpoints()
        atomic_write_json(self.round_dir / "round_config.json", self.config)

        if self.state.next_stage() == "INIT":
            self._checkpoint("INIT", self.config, {"round_dir": str(self.round_dir)})

        if self.state.next_stage() == "LOAD_ACTIVE_POLICY":
            registry = load_registry(self.registry_path)
            parent = get_active_policy(registry)
            expected = self.config.get("expected_parent_policy_hash")
            if expected and expected != parent.policy_hash:
                raise RoundRunnerViolation("configured parent policy hash is stale")
            atomic_write_json(self.round_dir / "active_parent.json", parent.to_dict())
            atomic_write_json(self.round_dir / "registry_before.json", registry)
            self._checkpoint("LOAD_ACTIVE_POLICY", registry["registry_hash"], parent.to_dict())

        parent = PolicyState.from_dict(read_json(self.round_dir / "active_parent.json"))

        if self.state.next_stage() == "RED_GENERATE":
            candidates = list(self.adapter.generate_red(parent, self.config))
            for row in candidates:
                if row.get("challenged_policy_hash") != parent.policy_hash:
                    raise RoundRunnerViolation("red candidate is not bound to active parent")
            _write_jsonl(self.round_dir / "red_candidates.jsonl", candidates)
            self.events.emit(
                "red",
                "red_candidates_generated",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                candidate_count=len(candidates),
                candidates_hash=hash_payload(candidates),
            )
            self._checkpoint("RED_GENERATE", parent.policy_hash, candidates)

        candidates = _read_jsonl(self.round_dir / "red_candidates.jsonl")
        if self.state.next_stage() == "VALIDITY_GATE":
            validity_rows = []
            valid = []
            for poison in candidates:
                prepared = dict(self.adapter.prepare_validity(poison))
                result = validity_gate(prepared)
                row = dict(prepared)
                row["validity"] = {
                    "proven_valid": result.proven_valid,
                    "checks": result.checks,
                    "rejection_reasons": result.rejection_reasons,
                    "evidence": result.evidence,
                    "result_hash": result.result_hash,
                }
                validity_rows.append(row)
                if result.proven_valid:
                    valid.append(row)
            _write_jsonl(self.round_dir / "validity_results.jsonl", validity_rows)
            _write_jsonl(self.round_dir / "valid_poisons.jsonl", valid)
            self.events.emit(
                "oracle",
                "validity_gate_completed",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                candidate_count=len(candidates),
                proven_valid_count=len(valid),
                results_hash=hash_payload(validity_rows),
            )
            self._checkpoint("VALIDITY_GATE", candidates, validity_rows)

        valid = _read_jsonl(self.round_dir / "valid_poisons.jsonl")
        if self.state.next_stage() == "BLUE_CHALLENGE":
            seeds = [int(seed) for seed in self.config.get("challenge_seeds", [1, 2, 3])]
            challenged = [
                evaluate_challenge(
                    parent,
                    poison,
                    seeds=seeds,
                    evaluator=self.adapter.evaluate_blue,
                )
                for poison in valid
            ]
            _write_jsonl(self.round_dir / "blue_challenge_results.jsonl", challenged)
            self.events.emit(
                "red",
                "blue_challenge_completed",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                result_count=len(challenged),
                results_hash=hash_payload(challenged),
            )
            self._checkpoint("BLUE_CHALLENGE", {"policy": parent.policy_hash, "seeds": seeds}, challenged)

        challenged = _read_jsonl(self.round_dir / "blue_challenge_results.jsonl")
        if self.state.next_stage() == "ARCHIVE_UPDATE":
            archive_before = load_archive(self.archive_path)
            archived = []
            for row in challenged:
                if row.get("hardness_class") not in {"hard_residual", "borderline_residual"}:
                    continue
                enriched = dict(row)
                enriched["learnability"] = str(self.adapter.probe_learnability(parent, row))
                enriched["novelty"] = novelty_score(enriched, archive_before + archived)
                enriched["archive_cell"] = archive_cell(enriched)
                if enriched["learnability"] not in {"reachable", "weakly_reachable"}:
                    continue
                archived.append(update_archive(self.archive_path, enriched))
            _write_jsonl(self.round_dir / "archive_updates.jsonl", archived)
            self.events.emit(
                "red",
                "residual_archive_updated",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                archived_count=len(archived),
                archive_updates_hash=hash_payload(archived),
            )
            self._checkpoint("ARCHIVE_UPDATE", challenged, archived)

        archived = _read_jsonl(self.round_dir / "archive_updates.jsonl")
        if self.state.next_stage() == "FREEZE_RESIDUAL_MANIFEST":
            residual_manifest = make_manifest(
                archived,
                split="residual",
                metadata={
                    "challenged_policy_id": parent.policy_id,
                    "challenged_policy_hash": parent.policy_hash,
                },
            )
            freeze_manifest(self.round_dir / "residual_manifest.json", residual_manifest)
            self._checkpoint("FREEZE_RESIDUAL_MANIFEST", archived, residual_manifest)

        residual_manifest = verify_manifest(read_json(self.round_dir / "residual_manifest.json"))
        if self.state.next_stage() == "SPLIT_ADAPT_TARGET":
            adaptation, target = grouped_split(
                residual_manifest,
                adaptation_fraction=float(self.config.get("adaptation_fraction", 0.5)),
                seed=int(self.config.get("split_seed", 0)),
            )
            freeze_manifest(self.round_dir / "adaptation_manifest.json", adaptation)
            freeze_manifest(self.round_dir / "target_manifest.json", target)
            self._checkpoint("SPLIT_ADAPT_TARGET", residual_manifest, {
                "adaptation": adaptation["manifest_hash"],
                "target": target["manifest_hash"],
            })

        adaptation = verify_manifest(read_json(self.round_dir / "adaptation_manifest.json"))
        target = verify_manifest(read_json(self.round_dir / "target_manifest.json"))
        if self.state.next_stage() == "PROPOSE_CHILDREN":
            search_space = read_json(self.root / self.config["policy_search_space"])
            children = propose_children(
                parent,
                adaptation,
                search_space,
                round_id=self.round_id,
                seed=int(self.config.get("policy_search_seed", 0)),
            )
            registry = load_registry(self.registry_path)
            for child in children:
                if child.policy_id not in registry["policies"]:
                    registry = register_candidate(
                        self.registry_path,
                        child,
                        ledger_path=self.ledger_path,
                        event_logger=self.events,
                        round_id=self.round_id,
                    )
            _write_jsonl(
                self.round_dir / "child_policies.jsonl",
                [child.to_dict() for child in children],
            )
            self._checkpoint("PROPOSE_CHILDREN", adaptation["manifest_hash"], [
                child.policy_hash for child in children
            ])

        children = [
            PolicyState.from_dict(row)
            for row in _read_jsonl(self.round_dir / "child_policies.jsonl")
        ]
        if self.state.next_stage() == "SCREEN_CHILDREN":
            screening = [
                {
                    "policy_id": child.policy_id,
                    "policy_hash": child.policy_hash,
                    **dict(self.adapter.screen_child(parent, child, adaptation)),
                }
                for child in children
            ]
            _write_jsonl(self.round_dir / "screening_results.jsonl", screening)
            self._checkpoint("SCREEN_CHILDREN", adaptation["manifest_hash"], screening)

        screening = _read_jsonl(self.round_dir / "screening_results.jsonl")
        survivors = {
            row["policy_id"] for row in screening if row.get("survive")
        }
        if self.state.next_stage() == "FREEZE_PROMOTION_MANIFEST":
            non_target_source = read_json(self.root / self.config["non_target_manifest"])
            verify_manifest(non_target_source)
            if non_target_source.get("split") != "non_target":
                raise RoundRunnerViolation("configured non-target manifest has wrong split")
            non_target = freeze_manifest(
                self.round_dir / "non_target_manifest.json", non_target_source
            )
            promotion = make_manifest(
                [
                    {"policy_id": child.policy_id, "policy_hash": child.policy_hash}
                    for child in children if child.policy_id in survivors
                ],
                split="promotion_candidates",
                source_manifest_hash=target["manifest_hash"],
                metadata={"non_target_manifest_hash": non_target["manifest_hash"]},
            )
            freeze_manifest(self.round_dir / "promotion_manifest.json", promotion)
            self._checkpoint("FREEZE_PROMOTION_MANIFEST", screening, promotion)

        non_target = verify_manifest(read_json(self.round_dir / "non_target_manifest.json"))
        promotion_manifest = verify_manifest(read_json(self.round_dir / "promotion_manifest.json"))
        if self.state.next_stage() == "PAIRED_REPLAY":
            replay_rows = []
            seeds = [int(seed) for seed in self.config.get("promotion_seeds", [11, 12, 13])]
            for child in children:
                if child.policy_id not in survivors:
                    continue
                for row in paired_replay(
                    parent,
                    child,
                    target_manifest=target,
                    non_target_manifest=non_target,
                    seeds=seeds,
                    evaluator=self.adapter.replay,
                ):
                    row["candidate_policy_id"] = child.policy_id
                    replay_rows.append(row)
            _write_jsonl(self.round_dir / "paired_validation.jsonl", replay_rows)
            self._checkpoint("PAIRED_REPLAY", promotion_manifest["manifest_hash"], replay_rows)

        replay_rows = _read_jsonl(self.round_dir / "paired_validation.jsonl")
        if self.state.next_stage() == "DECIDE":
            decisions = []
            for child in children:
                rows = [
                    row for row in replay_rows
                    if row.get("candidate_policy_id") == child.policy_id
                ]
                if not rows:
                    continue
                decisions.append(decide_policy_promotion(
                    parent,
                    child,
                    rows,
                    validation_manifest_hash=promotion_manifest["manifest_hash"],
                    thresholds=self.config.get("promotion_thresholds"),
                    provenance={
                        "round_id": self.round_id,
                        "residual_manifest_hash": residual_manifest["manifest_hash"],
                        "adaptation_manifest_hash": adaptation["manifest_hash"],
                        "target_manifest_hash": target["manifest_hash"],
                        "non_target_manifest_hash": non_target["manifest_hash"],
                        "paired_result_hash": hash_payload(rows),
                        "code_commit_sha": str(self.config.get("code_commit_sha") or ""),
                        "toolchain_fingerprint": dict(
                            getattr(self.adapter, "toolchain_fingerprint", {}) or {}
                        ),
                    },
                ))
            _write_jsonl(self.round_dir / "promotion_decisions.jsonl", decisions)
            winner = select_single_promotable_child(decisions)
            atomic_write_json(self.round_dir / "winner.json", winner or {})
            self._checkpoint("DECIDE", replay_rows, decisions)

        decisions = _read_jsonl(self.round_dir / "promotion_decisions.jsonl")
        winner = read_json(self.round_dir / "winner.json")
        if self.state.next_stage() == "ATOMIC_COMMIT":
            if winner:
                current_registry = load_registry(self.registry_path)
                current_active = get_active_policy(current_registry)
                if current_active.policy_id == winner["candidate_policy_id"]:
                    # A process may die after the atomic registry replace but
                    # before the round checkpoint. Treat the exact winner as an
                    # idempotently completed commit.
                    if current_active.policy_hash != winner["candidate_policy_hash"]:
                        raise RoundRunnerViolation("active winner policy hash mismatch")
                    registry_after = current_registry
                else:
                    registry_after = promote_policy(
                        self.registry_path,
                        winner["candidate_policy_id"],
                        winner,
                        ledger_path=self.ledger_path,
                        event_logger=self.events,
                        round_id=self.round_id,
                    )
            else:
                registry_after = load_registry(self.registry_path)
            decision_by_id = {
                row["candidate_policy_id"]: row for row in decisions
            }
            for child in children:
                if winner and child.policy_id == winner["candidate_policy_id"]:
                    continue
                entry = registry_after["policies"].get(child.policy_id)
                if entry and entry["status"] == "candidate":
                    decision = decision_by_id.get(child.policy_id, {})
                    registry_after = reject_policy(
                        self.registry_path,
                        child.policy_id,
                        decision.get("rejection_reasons") or ["screening_reject"],
                        provisional=decision.get("decision") == "provisional_candidate",
                        ledger_path=self.ledger_path,
                        event_logger=self.events,
                        round_id=self.round_id,
                    )
            atomic_write_json(self.round_dir / "registry_after.json", registry_after)
            self._checkpoint("ATOMIC_COMMIT", decisions, registry_after["registry_hash"])

        registry_after = load_registry(self.registry_path)
        active = get_active_policy(registry_after)
        if self.state.next_stage() == "RENEWED_CHALLENGE":
            binding = assert_renewed_challenge_binding(
                str(self.registry_path), active.policy_hash
            )
            atomic_write_json(self.round_dir / "renewed_challenge_binding.json", binding)
            self._checkpoint("RENEWED_CHALLENGE", registry_after["registry_hash"], binding)

        if self.state.next_stage() == "COMPLETE":
            summary = {
                "round_id": self.round_id,
                "parent_policy_id": parent.policy_id,
                "parent_policy_hash": parent.policy_hash,
                "active_policy_id": active.policy_id,
                "active_policy_hash": active.policy_hash,
                "promoted": active.policy_hash != parent.policy_hash,
                "residual_manifest_hash": residual_manifest["manifest_hash"],
                "target_manifest_hash": target["manifest_hash"],
                "registry_hash_after": registry_after["registry_hash"],
            }
            atomic_write_json(self.round_dir / "round_summary.json", summary)
            self._checkpoint("COMPLETE", active.policy_hash, summary)
        return read_json(self.round_dir / "round_summary.json")


def run_round(
    registry: str | Path,
    red_adapter: Any,
    blue_adapter: Any,
    manifests: dict[str, Any],
) -> dict[str, Any]:
    """Stable software interface for one authoritative evolution round.

    ``manifests`` carries paths/configuration plus ``round_id`` and optionally
    ``project_root``.  It does not grant either adapter registry authority.
    """
    config = dict(manifests)
    round_id = str(config.pop("round_id"))
    project_root = Path(str(config.pop("project_root", Path(registry).parent)))
    registry_path = Path(registry)
    try:
        config["policy_registry"] = str(registry_path.relative_to(project_root))
    except ValueError:
        config["policy_registry"] = str(registry_path)
    return EvolutionRoundRunner(
        config,
        round_id=round_id,
        adapter=_SplitEvolutionAdapter(red_adapter, blue_adapter),
        project_root=project_root,
    ).run()


def _read_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return read_json(path)
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RoundRunnerViolation("YAML config requires PyYAML; JSON is supported natively") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RoundRunnerViolation("round config must be an object")
    return payload


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--project-root", default=str(Path.cwd()))
    args = parser.parse_args()
    config = _read_config(Path(args.config))
    config.setdefault("project_root", args.project_root)
    adapter = _load_adapter(config["adapter"], config)
    result = EvolutionRoundRunner(
        config,
        round_id=args.round_id,
        adapter=adapter,
        project_root=args.project_root,
    ).run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
