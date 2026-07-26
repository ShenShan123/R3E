#!/usr/bin/env python3
"""Build deterministic, read-only provenance evidence for frozen-probe B3.

This auditor does not call an LLM and does not mutate the skill registry.  It
records what the current artifacts can actually prove, and fails closed when
the recorded promotion decision disagrees with the active registry.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ISO = ROOT / ".iso_semrepair"
REGISTRY = ISO / "skills.json"
REPLAY = ISO / "blue_abl" / "poison_pool.jsonl"
CURVE = ISO / "frozen_probe_curve.json"
RECORDED_DECISION = ISO / "promotion_gate.json"
SKILL_CODE = ROOT / "semantic_repair_bench" / "skill_registry.py"
CURVE_CODE = ROOT / "semantic_repair_bench" / "frozen_probe_curve.py"
GATE_CODE = ROOT / "semantic_repair_bench" / "promotion_gate.py"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(raw.encode())


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def literal_assignments(path: Path, names: set[str]) -> dict[str, Any]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(value)
    missing = names - found.keys()
    if missing:
        raise RuntimeError(f"missing literal assignments in {path}: {sorted(missing)}")
    return found


def normalized_replay(rows: list[dict]) -> list[dict]:
    normalized = []
    for index, row in enumerate(rows):
        item = {
            "case_index": index,
            "design_name": row.get("design_name"),
            "mutation_type": row.get("mutation_type"),
            "top_module": row.get("top_module"),
        }
        for key in ("buggy_rtl", "golden_rtl"):
            path = Path(row[key]) if row.get(key) else None
            item[f"{key}_sha256"] = file_sha(path) if path and path.is_file() else None
        tb_hashes = []
        for raw in row.get("tb_sources", []):
            path = Path(raw)
            tb_hashes.append(file_sha(path) if path.is_file() else None)
        item["tb_source_sha256"] = tb_hashes
        item["case_content_hash"] = canonical_sha(item)
        normalized.append(item)
    return normalized


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ISO / "b3_provenance_evidence.json")
    ap.add_argument("--replay-out", type=Path,
                    default=ISO / "b3_replay_manifest.normalized.jsonl")
    args = ap.parse_args()

    literals = literal_assignments(
        SKILL_CODE,
        {"_REPAIR_HINTS", "_FAMILY_KEYWORDS", "_BASELINE_POLICY"},
    )
    registry = jsonl(REGISTRY)
    replay_rows = jsonl(REPLAY)
    replay_normalized = normalized_replay(replay_rows)
    curve = json.loads(CURVE.read_text())
    recorded = json.loads(RECORDED_DECISION.read_text())

    promoted = [row for row in registry if row.get("status") == "promoted"]
    policy_payload = {
        "baseline_policy": literals["_BASELINE_POLICY"],
        "family_keywords": literals["_FAMILY_KEYWORDS"],
        "repair_hints": literals["_REPAIR_HINTS"],
        "promoted_registry": promoted,
    }
    registry_says_promoted = bool(promoted)
    decision_says_promote = bool(recorded.get("promote"))
    evidence = {
        "schema": "r3e_b3_provenance_v1",
        "scope": "Red-Fixed-12 frozen-probe B3 routed policy + strategy",
        "candidate_strategy": {
            "generator": "human-authored static Python literals",
            "automatic_distillation": False,
            "source": str(SKILL_CODE.relative_to(ROOT)),
            "symbol": "_REPAIR_HINTS",
            "inputs_recorded_at_generation_time": None,
            "current_strategy_payload": literals["_REPAIR_HINTS"],
            "source_sha256": file_sha(SKILL_CODE),
            "note": (
                "No candidate-generation runner or candidate artifact is linked to B3. "
                "The strategies are executable code literals, so human/code modification "
                "must be assumed rather than claiming automatic distillation."
            ),
        },
        "human_modification": {
            "present": True,
            "basis": "candidate strategies and promoted registry entries are static authored artifacts",
            "automatic_candidate_to_registry_link_found": False,
        },
        "replay_manifest": {
            "source": str(REPLAY.relative_to(ROOT)),
            "source_sha256": file_sha(REPLAY),
            "n_cases": len(replay_rows),
            "normalized_manifest": str(args.replay_out.relative_to(ROOT)),
            "normalized_manifest_sha256": canonical_sha(replay_normalized),
        },
        "replay_result": {
            "source": str(CURVE.relative_to(ROOT)),
            "source_sha256": file_sha(CURVE),
            "B3": curve.get("B3_routed+strategy"),
            "runner": str(CURVE_CODE.relative_to(ROOT)),
            "runner_sha256": file_sha(CURVE_CODE),
            "seed_semantics": "rep indices 0/1/2; provider deterministic seed not recorded",
        },
        "promotion_decision": {
            "recorded_source": str(RECORDED_DECISION.relative_to(ROOT)),
            "recorded_source_sha256": file_sha(RECORDED_DECISION),
            "recorded": recorded,
            "active_registry_has_promoted_entries": registry_says_promoted,
            "decision_consistent_with_registry": decision_says_promote == registry_says_promoted,
            "authoritative_automatic_decision_available": False,
            "gate_runner": str(GATE_CODE.relative_to(ROOT)),
            "gate_runner_sha256": file_sha(GATE_CODE),
            "verdict": "INCONSISTENT_FAIL_CLOSED" if decision_says_promote != registry_says_promoted
                       else "CONSISTENT_BUT_NOT_CAUSALLY_LINKED",
        },
        "policy": {
            "registry_source": str(REGISTRY.relative_to(ROOT)),
            "registry_file_sha256": file_sha(REGISTRY),
            "active_promoted_skill_ids": [row.get("skill_id") for row in promoted],
            "executable_policy_hash": canonical_sha(policy_payload),
            "hash_definition": "sha256(canonical JSON of baseline policy, family keywords, repair hints, promoted registry)",
        },
        "code_hashes": {
            str(path.relative_to(ROOT)): file_sha(path)
            for path in (SKILL_CODE, CURVE_CODE, GATE_CODE)
        },
        "overall_verdict": "B3_IS_REPLAYABLE_BUT_NOT_AUTOMATICALLY_DISTILLED_OR_PROMOTED",
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.replay_out.write_text("\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) for row in replay_normalized
    ) + "\n")
    # Record the actual byte hash after materialization as a second, transport-level hash.
    evidence["replay_manifest"]["normalized_file_sha256"] = file_sha(args.replay_out)
    args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "out": str(args.out),
        "replay_out": str(args.replay_out),
        "policy_hash": evidence["policy"]["executable_policy_hash"],
        "promotion_verdict": evidence["promotion_decision"]["verdict"],
        "overall_verdict": evidence["overall_verdict"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
