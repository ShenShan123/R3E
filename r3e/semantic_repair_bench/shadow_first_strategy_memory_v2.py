"""A1 v2: compile distilled trace families into bounded action policies."""
from __future__ import annotations

import re
from typing import Callable, Iterable

from semantic_repair_bench.formal_protocol import (
    candidate_hash,
    canonical_json,
    hash_payload,
    load_registry,
    policy_hash,
    utc_now,
)
from semantic_repair_bench.shadow_first_strategy_memory import (
    ShadowFirstStrategyMemory,
    _append_jsonl_fsync,
    _read_jsonl,
)
from semantic_repair_bench.strategy_action_primitives import compile_action_policy
from semantic_repair_bench.strategy_reasoning_ir import validate_strategy_ir


class ShadowFirstStrategyMemoryV2(ShadowFirstStrategyMemory):
    """Preserve A1 gates while making a candidate policy executable."""

    def distill(
        self,
        entries: Iterable[dict],
        distiller: Callable[[dict, list[dict]], dict],
    ) -> list[dict]:
        registry = load_registry(self.registry_path, formal_mode=True)
        parent_policy = policy_hash(registry)
        registry_parent = hash_payload(registry)
        rollback = self._make_rollback_snapshot(registry)
        existing = _read_jsonl(self.candidates_path)
        fingerprints = {
            hash_payload({
                "trigger": row.get("trigger"),
                "strategy": row.get("normalized_strategy"),
                "action_policy": row.get("action_policy"),
            })
            for row in existing
        }
        candidates = []
        for group in self.group_shadow(entries):
            trigger = {
                "effect": group[0]["effect"],
                "bug_type": group[0]["bug_type"],
                "scope": group[0]["scope"],
            }
            prompt_payload = {
                "task": "select a reasoning-only repair primitive from the frozen catalog",
                "planner_authority": "select_repair_primitive_only",
                "forbidden_outputs": [
                    "start_line", "end_line", "new_code", "patch", "signal_name",
                    "module_name", "rtl_fragment",
                ],
                "trigger": trigger,
                "source_trajectories": [
                    {
                        "red_trace_hash": row["red_trace_hash"],
                        "repair_patch_hash": row["repair_patch_hash"],
                        "oracle_ok": row["oracle_ok"],
                        "effect": row["effect"],
                        "bug_type": row["bug_type"],
                        "scope": row["scope"],
                    }
                    for row in sorted(group, key=lambda item: item["trajectory_id"])
                ],
            }
            prompt = canonical_json(prompt_payload)
            raw = distiller(prompt_payload, group)
            reasoning_ir = validate_strategy_ir(raw, trigger)
            normalized = canonical_json(reasoning_ir)
            action_policy = compile_action_policy(trigger, group, reasoning_ir)
            fingerprint = hash_payload({
                "trigger": trigger,
                "strategy": normalized,
                "action_policy": action_policy,
            })
            if fingerprint in fingerprints:
                self._log({"trajectory_id": group[0]["trajectory_id"]}, "candidate-deduplicated",
                          fingerprint=fingerprint)
                continue
            forbidden_identifiers = sorted({
                str(row[field])
                for row in group
                for field in ("trajectory_id", "case_id", "design_id")
                if row.get(field)
            })
            candidate = {
                "artifact_id": f"auto_{trigger['bug_type']}_{fingerprint[:12]}",
                "origin": "automatic",
                # Keep the frozen formal-registry schema value.  Executability is
                # carried by the hash-bound action_policy below and by the
                # candidate-distilled-and-compiled ledger event.
                "generation_mode": "distilled",
                "source_trajectory_ids": sorted(row["trajectory_id"] for row in group),
                "source_residual_ids": sorted(row["trajectory_id"] for row in group),
                "source_residual_hashes": {
                    str(row["trajectory_id"]): str(row["trajectory_hash"])
                    for row in sorted(group, key=lambda item: item["trajectory_id"])
                },
                "candidate_prompt": prompt,
                "candidate_prompt_hash": hash_payload(prompt_payload),
                "candidate_raw_output": raw,
                "candidate_output_hash": hash_payload(raw),
                "normalized_strategy": normalized,
                "strategy_reasoning_ir": reasoning_ir,
                "trigger": trigger,
                "action_policy": action_policy,
                "parent_policy_hash": parent_policy,
                "registry_parent_hash": registry_parent,
                "validation_manifest_hash": hash_payload({
                    "target": self.target_manifest_hash,
                    "non_target": self.non_target_manifest_hash,
                }),
                "target_manifest_hash": self.target_manifest_hash,
                "non_target_manifest_hash": self.non_target_manifest_hash,
                "rollback_artifact": rollback,
                "promotion_decision_hash": "",
                "created_at": utc_now(),
                "runtime_status": "candidate",
                "leakage_detected": any(
                    re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])",
                        normalized,
                    )
                    for identifier in forbidden_identifiers
                ),
                "leakage_audit_hash": hash_payload(forbidden_identifiers),
            }
            candidate["candidate_hash"] = candidate_hash(candidate)
            _append_jsonl_fsync(self.candidates_path, candidate)
            fingerprints.add(fingerprint)
            candidates.append(candidate)
            self._log(candidate, "candidate-distilled-and-compiled",
                      parent_policy_hash=parent_policy,
                      strategy_primitives=action_policy.get("strategy_primitives", []))
        return candidates
