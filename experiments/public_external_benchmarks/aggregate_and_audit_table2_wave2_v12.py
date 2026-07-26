#!/usr/bin/env python3
"""Merge and audit the terminal Wave2 V12 native-APR evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WAVE2_ROOT = ROOT / ".cross_benchmark_r3e_202607/wave2_recovery_20260716_v12"
INVENTORY = WAVE2_ROOT / "INVENTORY.json"
PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/WAVE2_RECOVERY_PROTOCOL_20260716_V12.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path, *, allow_missing: bool = False) -> list[dict[str, Any]]:
    if allow_missing and not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def manifest_case_id(row: dict[str, Any]) -> str:
    value = row.get("case_id", row.get("task_id"))
    require(isinstance(value, str) and value, "manifest row has no case identifier")
    return value


def canonical_success_is_consistent(row: dict[str, Any]) -> bool:
    gate_success = any(candidate.get("gate", {}).get("ok") is True
                       for candidate in row.get("candidates", []))
    return bool(row.get("success")) == gate_success


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    inventory = json.loads(INVENTORY.read_text())
    require(inventory.get("schema") == "r3e-table2-wave2-recovery-inventory-v3",
            "unexpected V12 inventory schema")
    require(inventory.get("base_missing_jobs") == 176, "base missing count drift")
    require((inventory.get("jobs"), inventory.get("immediate_jobs"),
             inventory.get("deferred_jobs")) == (0, 0, 0),
            "V12 still contains runnable jobs")
    reused = inventory.get("reused_recovery_rows", [])
    terminal = inventory.get("terminal_failure_rows", [])
    require((len(reused), len(terminal)) == (150, 26),
            "unexpected V12 recovery partition")
    require(len(inventory.get("cells", [])) == 11, "unexpected Wave2 cell count")

    terminal_path = WAVE2_ROOT / "TERMINAL_FAILURES.jsonl"
    require(sha256_file(terminal_path) == inventory["terminal_failures_sha256"],
            "terminal failure file hash drift")
    require(sha256_file(Path(inventory["terminal_failure_decision_file"])) ==
            inventory["terminal_failure_decision_sha256"],
            "terminal decision file hash drift")
    require(sha256_file(Path(inventory["terminal_failure_prefix_rule_file"])) ==
            inventory["terminal_failure_prefix_rule_sha256"],
            "terminal prefix-rule hash drift")

    recovered_by_cell: dict[tuple[str, str, int | None], list[dict[str, Any]]] = defaultdict(list)
    source_hashes: dict[str, str] = {}
    for item in reused:
        canonical_path = Path(item["canonical"])
        supervisor_path = Path(item["supervisor"])
        require(sha256_file(canonical_path) == item["canonical_sha256"],
                f"recovery canonical hash drift: {canonical_path}")
        require(sha256_file(supervisor_path) == item["supervisor_sha256"],
                f"recovery supervisor hash drift: {supervisor_path}")
        supervisor = json.loads(supervisor_path.read_text())
        require(supervisor.get("status") == "complete" and supervisor.get("return_code") == 0,
                f"recovery supervisor incomplete: {supervisor_path}")
        rows = read_jsonl(canonical_path)
        require(len(rows) == 1, f"recovery artifact must contain one case: {canonical_path}")
        row = rows[0]
        require(row.get("case_id") == item["case_id"] and
                bool(row.get("success")) == bool(item["success"]),
                f"recovery inventory/canonical mismatch: {canonical_path}")
        require(canonical_success_is_consistent(row),
                f"recovery success/gate mismatch: {canonical_path}")
        key = (item["method"], item["benchmark"], item.get("seed"))
        recovered_by_cell[key].append({
            **row,
            "benchmark": item["benchmark"],
            "evidence_source": "recovery_complete",
            "source_canonical": str(canonical_path),
            "source_canonical_sha256": item["canonical_sha256"],
            "source_supervisor": str(supervisor_path),
            "source_supervisor_sha256": item["supervisor_sha256"],
        })
        source_hashes[str(canonical_path)] = item["canonical_sha256"]

    terminal_by_cell: dict[tuple[str, str, int | None], list[dict[str, Any]]] = defaultdict(list)
    terminal_classes: Counter[str] = Counter()
    for item in terminal:
        require(item.get("success") is False, "terminal row must be unrepaired")
        classification = str(item.get("classification"))
        terminal_classes[classification] += 1
        if "source_supervisor" in item:
            require(sha256_file(Path(item["source_supervisor"])) ==
                    item["source_supervisor_sha256"], "terminal supervisor hash drift")
            require(str(item.get("reporting_rule", "")).startswith("count_as_unrepaired"),
                    "terminal resource failure reporting-rule drift")
        else:
            require(sha256_file(Path(item["incident_supervisor"])) ==
                    item["incident_supervisor_sha256"], "SHA3 incident hash drift")
            require(sha256_file(Path(item["source_prefix_rule"])) ==
                    item["source_prefix_rule_sha256"], "SHA3 prefix-rule hash drift")
            require(classification == "not_run_due_host_resource_risk",
                    "unexpected prefix terminal classification")
            require("count as unrepaired" in str(item.get("reporting_rule", "")),
                    "SHA3 missing-as-failure rule absent")
        key = (item["method"], item["benchmark"], item.get("seed"))
        terminal_by_cell[key].append(item)

    merged_rows: list[dict[str, Any]] = []
    cell_results: list[dict[str, Any]] = []
    all_case_keys: set[tuple[str, str, int | None, str]] = set()
    for cell in inventory["cells"]:
        key = (cell["method"], cell["benchmark"], cell.get("seed"))
        manifest_path = Path(cell["manifest"])
        require(sha256_file(manifest_path) == cell["manifest_sha256"],
                f"manifest hash drift: {manifest_path}")
        manifest_rows = read_jsonl(manifest_path)
        manifest_by_id = {manifest_case_id(row): row for row in manifest_rows}
        require(len(manifest_by_id) == cell["target_cases"], "manifest coverage drift")

        original_path = Path(cell["original_canonical"])
        original_rows = read_jsonl(original_path, allow_missing=True)
        require(len(original_rows) == cell["completed_cases"],
                f"original row count drift: {key}")
        original_hash = sha256_file(original_path) if original_path.exists() else None
        if original_hash is not None:
            source_hashes[str(original_path)] = original_hash
        recovered_rows = recovered_by_cell[key]
        terminal_rows = terminal_by_cell[key]
        require(len(recovered_rows) + len(terminal_rows) == cell["missing_cases"],
                f"recovery/terminal count does not close cell: {key}")

        selected: list[dict[str, Any]] = []
        for row in original_rows:
            require(canonical_success_is_consistent(row),
                    f"original success/gate mismatch: {key}/{row.get('case_id')}")
            selected.append({
                **row,
                "benchmark": cell["benchmark"],
                "evidence_source": "original_complete",
                "source_canonical": str(original_path),
                "source_canonical_sha256": original_hash,
            })
        selected.extend(recovered_rows)
        for row in terminal_rows:
            case_id = row["case_id"]
            manifest_row = manifest_by_id[case_id]
            selected.append({
                "schema": "r3e-table2-wave2-terminal-case-v12",
                "method": cell["method"],
                "benchmark": cell["benchmark"],
                "seed": cell.get("seed"),
                "case_id": case_id,
                "case_sha256": manifest_row["case_sha256"],
                "manifest_sha256": cell["manifest_sha256"],
                "success": False,
                "candidate_count": 0,
                "candidates": [],
                "failure_reason": row["failure_reason"],
                "terminal_classification": row["classification"],
                "reporting_rule": row["reporting_rule"],
                "evidence_source": "terminal_failure_counted_unrepaired",
                "terminal_evidence": row,
            })

        ids = [row["case_id"] for row in selected]
        require(len(selected) == cell["target_cases"], f"fixed denominator incomplete: {key}")
        require(len(ids) == len(set(ids)) and set(ids) == set(manifest_by_id),
                f"case coverage/uniqueness failure: {key}")
        for row in selected:
            case_key = (*key, row["case_id"])
            require(case_key not in all_case_keys, f"duplicate final grain: {case_key}")
            all_case_keys.add(case_key)
            require(row.get("method") == cell["method"] and row.get("seed") == cell.get("seed"),
                    f"method/seed mismatch: {case_key}")
            require(row.get("case_sha256") == manifest_by_id[row["case_id"]]["case_sha256"],
                    f"case hash mismatch: {case_key}")
            require(row.get("manifest_sha256") == cell["manifest_sha256"],
                    f"manifest identity mismatch: {case_key}")
            for candidate in row.get("candidates", []):
                candidate_path = candidate.get("candidate_path")
                patch_hash = candidate.get("patch_sha256")
                if candidate_path is not None and patch_hash is not None:
                    require(sha256_file(Path(candidate_path)) == patch_hash,
                            f"candidate patch hash drift: {candidate_path}")
            merged_rows.append(row)

        successes = sum(bool(row["success"]) for row in selected)
        terminal_count = sum(row["evidence_source"] ==
                             "terminal_failure_counted_unrepaired" for row in selected)
        cell_results.append({
            "method": cell["method"], "benchmark": cell["benchmark"],
            "seed": cell.get("seed"), "repaired": successes,
            "denominator": cell["target_cases"],
            "repair_percent": 100.0 * successes / cell["target_cases"],
            "original_complete_rows": len(original_rows),
            "recovery_complete_rows": len(recovered_rows),
            "terminal_failure_rows": terminal_count,
        })

    require(len(merged_rows) == 269 and len(all_case_keys) == 269,
            "unexpected final Wave2 case grain")
    require(sum(row["evidence_source"] == "original_complete" for row in merged_rows) == 93,
            "original source total drift")
    require(sum(row["evidence_source"] == "recovery_complete" for row in merged_rows) == 150,
            "recovery source total drift")
    require(sum(row["evidence_source"] == "terminal_failure_counted_unrepaired"
                for row in merged_rows) == 26, "terminal source total drift")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in cell_results:
        grouped[(row["method"], row["benchmark"])].append(row)
    aggregates: list[dict[str, Any]] = []
    for (method, benchmark), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: -1 if row["seed"] is None else int(row["seed"]))
        percentages = [float(row["repair_percent"]) for row in rows]
        entry: dict[str, Any] = {
            "method": method, "benchmark": benchmark, "runs": rows,
            "repair_percent_mean": statistics.mean(percentages),
            "randomness_protocol": "seeds_101_202_303" if len(rows) == 3 else "deterministic_once",
        }
        entry["repair_percent_sample_sd"] = (
            statistics.stdev(percentages) if len(percentages) > 1 else None
        )
        aggregates.append(entry)

    merged_path = out / "canonical_case_results.jsonl"
    merged_rows.sort(key=lambda row: (
        row["method"], row["benchmark"],
        -1 if row.get("seed") is None else int(row["seed"]), row["case_id"]
    ))
    merged_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                   for row in merged_rows))
    aggregate = {
        "schema": "r3e-table2-wave2-native-apr-aggregate-v12",
        "metric": "fixed-manifest-denominator functional repair rate; terminal and unrun SHA3 rows count unrepaired",
        "cells": aggregates,
        "coverage": {
            "method_benchmark_run_cells": len(cell_results),
            "method_benchmark_aggregates": len(aggregates),
            "canonical_case_rows": len(merged_rows),
            "original_complete_rows": 93,
            "recovery_complete_rows": 150,
            "terminal_failure_rows": 26,
            "not_run_sha3_rows": terminal_classes["not_run_due_host_resource_risk"],
        },
        "terminal_classifications": dict(sorted(terminal_classes.items())),
        "canonical_case_results_sha256": sha256_file(merged_path),
        "source_canonical_hashes": dict(sorted(source_hashes.items())),
        "historical_artifacts_overwritten": False,
    }
    aggregate_path = out / "AGGREGATE.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")

    audit = {
        "schema": "r3e-table2-wave2-native-apr-formal-audit-v12",
        "verdict": "PASS",
        "grain": "method x benchmark x seed-or-deterministic x frozen case",
        "coverage": aggregate["coverage"],
        "quality_checks": {
            "v12_has_zero_remaining_jobs": True,
            "original_recovery_terminal_partition_closes_all_cells": True,
            "fixed_manifest_denominators_complete": True,
            "case_ids_unique_and_equal_manifests": True,
            "case_manifest_hashes_match": True,
            "recovery_supervisors_complete_return_zero": True,
            "source_canonical_and_supervisor_hashes_match": True,
            "candidate_patch_hashes_match_when_present": True,
            "canonical_success_matches_unified_gate": True,
            "terminal_rows_are_unrepaired": True,
            "unrun_sha3_rows_are_disclosed_not_native_returns": True,
        },
        "reporting_rule": (
            "Use every frozen benchmark denominator. Count resource-terminal and unrun SHA3 cases "
            "as unrepaired; never describe unrun SHA3 cases as native algorithm/evaluator returns."
        ),
        "protocol_sha256": sha256_file(PROTOCOL),
        "inventory_sha256": sha256_file(INVENTORY),
        "terminal_failures_sha256": sha256_file(terminal_path),
        "aggregate_sha256": sha256_file(aggregate_path),
        "canonical_case_results_sha256": sha256_file(merged_path),
        "network_or_model_calls": 0,
        "historical_artifacts_overwritten": False,
    }
    audit_path = out / "FORMAL_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": "PASS", "aggregate": str(aggregate_path),
                      "audit": str(audit_path), "cells": aggregates}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
