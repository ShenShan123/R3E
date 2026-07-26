#!/usr/bin/env python3
"""Materialize explicit fixed-denominator overlays and freeze the final Table 2."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from table2_common import read_jsonl, sha256_file, write_jsonl


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".cross_benchmark_r3e_202607/table2_final_20260723_v2"
MANIFESTS = ROOT / "datasets/manifests"
FLASH_DIRECT = ROOT / ".cross_benchmark_r3e_202607/table2_flash_direct_prefix_recovery_20260723_v1"
FLASH_R3E = ROOT / ".cross_benchmark_r3e_202607/table2_feedback_prefix_recovery_20260722_v1"
PRO = ROOT / ".cross_benchmark_r3e_202607/table2_deepseek_v4_pro_prefix_recovery_20260722_v1"
NATIVE = ROOT / ".cross_benchmark_r3e_202607/wave2_recovery_20260716_v12/formal_aggregate"
CF39 = ROOT / ".iso_semrepair/cf_full_result.jsonl"
RR39 = ROOT / ".iso_semrepair/rr_full_result.jsonl"
STATE = ROOT / ".iso_semrepair/skills.json"
DENOMINATORS = {"cirfix39": 39, "literature32": 32, "strider14": 14}
SEEDS = (101, 202, 303, 404, 505)


def manifest_case_id(row: dict[str, Any]) -> str:
    value = row.get("case_id") or row.get("task_id")
    if not value:
        raise RuntimeError("manifest row lacks case identifier")
    return str(value)


def materialize_flash_r3e() -> list[dict[str, Any]]:
    source = [row for row in read_jsonl(FLASH_R3E / "CANONICAL_CASES.jsonl") if row.get("arm") == "r3e"]
    if len(source) != 405:
        raise RuntimeError(f"Flash R3E executed panel drift: {len(source)}")
    result: list[dict[str, Any]] = []
    source_by_cell: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in source:
        key = (row["benchmark"], int(row["seed"]))
        if row["case_id"] in source_by_cell[key]:
            raise RuntimeError(f"duplicate Flash R3E case: {key}/{row['case_id']}")
        source_by_cell[key][row["case_id"]] = row
    for benchmark, denominator in DENOMINATORS.items():
        manifest_path = MANIFESTS / f"{benchmark}.jsonl"
        manifest = read_jsonl(manifest_path)
        manifest_by_id = {manifest_case_id(row): row for row in manifest}
        if len(manifest_by_id) != denominator:
            raise RuntimeError(f"manifest denominator drift: {benchmark}/{len(manifest_by_id)}")
        for seed in SEEDS:
            observed = source_by_cell[(benchmark, seed)]
            extra = set(observed) - set(manifest_by_id)
            if extra:
                raise RuntimeError(f"Flash R3E extra cases: {benchmark}/{seed}/{sorted(extra)}")
            for case_id, manifest_row in manifest_by_id.items():
                if case_id in observed:
                    row = dict(observed[case_id])
                    row.update({
                        "schema": "r3e-table2-explicit-fixed-denominator-case-v1",
                        "method": "r3e_deepseek_v4_flash",
                        "execution": "executed",
                        "pass_within_budget": bool(row.pop("within_3")),
                        "source_panel_sha256": sha256_file(FLASH_R3E / "CANONICAL_CASES.jsonl"),
                    })
                    row.pop("arm", None)
                else:
                    row = {
                        "schema": "r3e-table2-explicit-fixed-denominator-case-v1",
                        "method": "r3e_deepseek_v4_flash",
                        "benchmark": benchmark,
                        "seed": seed,
                        "case_id": case_id,
                        "case_sha256": manifest_row.get("case_sha256"),
                        "execution": "not_run",
                        "artifact_source": "operational_sha3_exclusion",
                        "candidate_rows": 0,
                        "pass_at_1": False,
                        "pass_within_budget": False,
                        "failure_reason": "operational_sha3_exclusion_counted_unrepaired",
                        "reporting_rule": "count unrepaired on exact Literature-32 denominator",
                        "manifest_sha256": sha256_file(manifest_path),
                        "source_panel_sha256": sha256_file(FLASH_R3E / "CANONICAL_CASES.jsonl"),
                    }
                result.append(row)
    result.sort(key=lambda row: (row["benchmark"], row["seed"], row["case_id"]))
    if len(result) != 425:
        raise RuntimeError(f"explicit Flash R3E panel drift: {len(result)}")
    exclusions = [row for row in result if row.get("execution") == "not_run"]
    if len(exclusions) != 20 or Counter(row["benchmark"] for row in exclusions) != {"literature32": 20}:
        raise RuntimeError(f"explicit exclusion drift: {len(exclusions)}")
    for benchmark, denominator in DENOMINATORS.items():
        for seed in SEEDS:
            rows = [row for row in result if row["benchmark"] == benchmark and row["seed"] == seed]
            if len(rows) != denominator or len({row["case_id"] for row in rows}) != denominator:
                raise RuntimeError(f"explicit coverage drift: {benchmark}/{seed}")
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "FLASH_R3E_CANONICAL_CASES_425.jsonl"
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in result)
    if target.exists() and target.read_text() != text:
        raise RuntimeError(f"refusing to alter explicit overlay: {target}")
    if not target.exists():
        target.write_text(text)
    audit = {
        "schema": "r3e-table2-flash-r3e-explicit-denominator-audit-v1",
        "verdict": "PASS",
        "canonical_case_rows": len(result),
        "executed_rows": len(result) - len(exclusions),
        "explicit_not_run_rows": len(exclusions),
        "per_seed_denominators": DENOMINATORS,
        "source_cases_sha256": sha256_file(FLASH_R3E / "CANONICAL_CASES.jsonl"),
        "explicit_cases_sha256": sha256_file(target),
        "network_or_model_calls": 0,
    }
    audit_path = OUT / "FLASH_R3E_EXPLICIT_DENOMINATOR_AUDIT.json"
    payload = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    if audit_path.exists() and audit_path.read_text() != payload:
        raise RuntimeError("explicit overlay audit drift")
    if not audit_path.exists():
        audit_path.write_text(payload)
    print(json.dumps(audit, indent=2, sort_keys=True))
    return result


def five_seed_summary(rows: list[dict[str, Any]], method: str, metric: str) -> list[dict[str, Any]]:
    output = []
    for benchmark, denominator in DENOMINATORS.items():
        counts = []
        exclusions = []
        for seed in SEEDS:
            cell = [row for row in rows if row["benchmark"] == benchmark and int(row["seed"]) == seed]
            if len(cell) != denominator or len({row["case_id"] for row in cell}) != denominator:
                raise RuntimeError(f"coverage drift: {method}/{benchmark}/{seed}/{len(cell)}")
            counts.append(sum(bool(row.get(metric)) for row in cell))
            exclusions.append(sum(row.get("execution") == "not_run" for row in cell))
        rates = [100 * count / denominator for count in counts]
        output.append({
            "method": method,
            "benchmark": benchmark,
            "metric": metric,
            "denominator": denominator,
            "seeds": list(SEEDS),
            "repaired_counts": counts,
            "not_run_counted_unrepaired_per_seed": exclusions,
            "mean_percent": statistics.mean(rates),
            "sample_sd_percent": statistics.stdev(rates),
        })
    return output


def native_rows() -> list[dict[str, Any]]:
    aggregate = json.loads((NATIVE / "AGGREGATE.json").read_text())
    rows: list[dict[str, Any]] = []
    for cell in aggregate["cells"]:
        rows.append({
            "method": cell["method"],
            "benchmark": cell["benchmark"],
            "protocol": cell["randomness_protocol"],
            "repaired_counts": [run["repaired"] for run in cell["runs"]],
            "denominators": [run["denominator"] for run in cell["runs"]],
            "mean_percent": cell["repair_percent_mean"],
            "sample_sd_percent": cell["repair_percent_sample_sd"],
            "terminal_failure_counts": [run["terminal_failure_rows"] for run in cell["runs"]],
        })
    cf = read_jsonl(CF39); rr = read_jsonl(RR39)
    if len(cf) != 39 or len(rr) != 39:
        raise RuntimeError("CirFix-39 native source coverage drift")
    cf_oracle_repaired = sum(bool(row.get("og_ok")) for row in cf)
    rr_oracle_repaired = sum(bool(row.get("og_ok")) for row in rr)
    if (cf_oracle_repaired, rr_oracle_repaired) != (15, 22):
        raise RuntimeError("CirFix-39 unified-oracle count drift")
    rows.extend([
        {"method": "cirfix", "benchmark": "cirfix39", "protocol": "historical_native_once",
         "repaired_counts": [cf_oracle_repaired], "denominators": [39],
         "mean_percent": 100 * cf_oracle_repaired / 39,
         "sample_sd_percent": None, "terminal_failure_counts": [sum(row.get("status") == "timeout" for row in cf)]},
        {"method": "rtl_repair", "benchmark": "cirfix39", "protocol": "deterministic_once",
         "repaired_counts": [rr_oracle_repaired], "denominators": [39],
         "mean_percent": 100 * rr_oracle_repaired / 39,
         "sample_sd_percent": None, "terminal_failure_counts": [0]},
    ])
    if len(rows) != 9:
        raise RuntimeError(f"native Table 2 cell drift: {len(rows)}")
    return sorted(rows, key=lambda row: (row["method"], row["benchmark"]))


def fmt(value: float, sd: float | None, dagger: bool) -> str:
    if sd is None:
        text = f"{value:.1f}%"
    else:
        text = f"{value:.2f}±{sd:.2f}%"
    return text + ("†" if dagger else "")


def finalize() -> dict[str, Any]:
    explicit_r3e = materialize_flash_r3e()
    direct_audit = json.loads((FLASH_DIRECT / "FORMAL_AUDIT.json").read_text())
    flash_audit = json.loads((FLASH_R3E / "FORMAL_AUDIT.json").read_text())
    pro_audit = json.loads((PRO / "FORMAL_AUDIT.json").read_text())
    native_audit = json.loads((NATIVE / "FORMAL_AUDIT.json").read_text())
    audits = {"flash_direct": direct_audit, "flash_r3e": flash_audit, "pro": pro_audit, "native": native_audit}
    if any(value.get("verdict") != "PASS" for value in audits.values()):
        raise RuntimeError({name: value.get("verdict") for name, value in audits.items()})
    direct = read_jsonl(FLASH_DIRECT / "FLASH_DIRECT_CANONICAL_CASES_425.jsonl")
    pro_all = read_jsonl(PRO / "CANONICAL_CASES.jsonl")
    pro_direct = [row for row in pro_all if row["method"] == "direct_llm_deepseek_v4_pro"]
    pro_r3e = [row for row in pro_all if row["method"] == "r3e_deepseek_v4_pro"]
    summaries = []
    summaries += five_seed_summary(direct, "direct_llm_deepseek_v4_flash", "pass_at_1")
    summaries += five_seed_summary(explicit_r3e, "r3e_deepseek_v4_flash", "pass_within_budget")
    summaries += five_seed_summary(pro_direct, "direct_llm_deepseek_v4_pro", "pass_at_1")
    summaries += five_seed_summary(pro_r3e, "r3e_deepseek_v4_pro", "pass_within_budget")
    by = {(row["method"], row["benchmark"]): row for row in summaries}
    native = native_rows()
    native_by = {(row["method"], row["benchmark"]): row for row in native}
    traditional_meta = {
        "cirfix": "Native genetic repair",
        "rtl_repair": "Native symbolic repair",
        "strider": "Native trace-guided repair",
    }
    table_rows = []
    for method in ("cirfix", "rtl_repair", "strider"):
        cells = {}
        for benchmark in DENOMINATORS:
            cell = native_by[(method, benchmark)]
            dagger = benchmark == "literature32" and any(cell["terminal_failure_counts"])
            cells[benchmark] = fmt(cell["mean_percent"], cell["sample_sd_percent"], dagger)
        table_rows.append({"section": "Traditional RTL APR", "method": method.replace("rtl_repair", "RTL-Repair").replace("cirfix", "CirFix").replace("strider", "Strider"),
                           "search_protocol": traditional_meta[method], "cells": cells})
    llm_meta = [
        ("direct_llm_deepseek_v4_flash", "Direct LLM", "DeepSeek-v4-flash; 1 call; pass@1"),
        ("r3e_deepseek_v4_flash", "R³E", "DeepSeek-v4-flash; ≤3 verified revisions; within-3"),
        ("direct_llm_deepseek_v4_pro", "Direct LLM", "DeepSeek-v4-Pro; 1 call; pass@1"),
        ("r3e_deepseek_v4_pro", "R³E", "DeepSeek-v4-Pro; ≤3 verified revisions; within-3"),
    ]
    for key, label, protocol in llm_meta:
        cells = {}
        for benchmark in DENOMINATORS:
            cell = by[(key, benchmark)]
            dagger = any(cell["not_run_counted_unrepaired_per_seed"])
            cells[benchmark] = fmt(cell["mean_percent"], cell["sample_sd_percent"], dagger)
        table_rows.append({"section": "LLM-based repair", "method": label,
                           "search_protocol": protocol, "cells": cells})
    table = {
        "schema": "r3e-final-table2-v1",
        "title": "Cross-benchmark RTL functional repair rate (%)",
        "fixed_denominators": DENOMINATORS,
        "llm_reporting": "five-seed mean ± sample SD in percentage points",
        "native_reporting": "CirFix cross-benchmark cells use seeds 101/202/303; deterministic methods run once",
        "dagger": "operational exclusion or resource-terminal case counted unrepaired on frozen denominator",
        "rows": table_rows,
    }
    (OUT / "FINAL_TABLE2.json").write_text(json.dumps(table, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    write_jsonl(OUT / "FINAL_LLM_PER_SEED_SUMMARY.jsonl", summaries)
    write_jsonl(OUT / "FINAL_NATIVE_CELL_SUMMARY.jsonl", native)
    lines = ["# Final Table 2", "", "| Method | Search protocol | CirFix-39 ↑ | Literature-32 ↑ | Strider-14 ↑ |",
             "|---|---|---:|---:|---:|"]
    section = None
    for row in table_rows:
        if row["section"] != section:
            section = row["section"]
            lines.append(f"| **{section}** |  |  |  |  |")
        cells = row["cells"]
        method = f"**{row['method']}**" if row["method"] == "R³E" else row["method"]
        lines.append(f"| {method} | {row['search_protocol']} | {cells['cirfix39']} | {cells['literature32']} | {cells['strider14']} |")
    lines += ["", "† Operational exclusion/resource-terminal case counted unrepaired on the frozen manifest denominator.", ""]
    (OUT / "FINAL_TABLE2.md").write_text("\n".join(lines))
    source_paths = [
        Path(__file__).resolve(),
        ROOT / ".cross_benchmark_r3e_202607/TABLE2_FLASH_DIRECT_PREFIX_RECOVERY_PROTOCOL_20260723_V1.json",
        FLASH_DIRECT / "FORMAL_AUDIT.json", FLASH_DIRECT / "FLASH_DIRECT_CANONICAL_CASES_425.jsonl",
        FLASH_R3E / "FORMAL_AUDIT.json", FLASH_R3E / "CANONICAL_CASES.jsonl",
        PRO / "FORMAL_AUDIT.json", PRO / "CANONICAL_CASES.jsonl",
        NATIVE / "FORMAL_AUDIT.json", NATIVE / "canonical_case_results.jsonl", CF39, RR39,
        OUT / "FLASH_R3E_CANONICAL_CASES_425.jsonl",
    ]
    errors = []
    if len(direct) != 425 or len(explicit_r3e) != 425 or len(pro_all) != 850:
        errors.append("final LLM panel cardinality mismatch")
    if any(len({(row["benchmark"], row["seed"], row["case_id"]) for row in panel}) != len(panel)
           for panel in (direct, explicit_r3e, pro_direct, pro_r3e)):
        errors.append("duplicate final LLM case key")
    candidate0 = read_jsonl(FLASH_DIRECT / "FLASH_DIRECT_CANDIDATE0_425.jsonl")
    if any(str(row.get("status", "")).startswith("api_") for row in candidate0):
        errors.append("Flash Direct candidate-0 transport failure remains")
    if pro_audit.get("canonical_transport_api_failures") != 0 or flash_audit.get("canonical_transport_api_failures") != 0:
        errors.append("recovered R3E/Pro transport failure remains")
    audit = {
        "schema": "r3e-final-table2-formal-audit-v1",
        "verdict": "PASS" if not errors else "FAIL",
        "errors": errors,
        "source_audit_verdicts": {name: value["verdict"] for name, value in audits.items()},
        "flash_direct_case_rows": len(direct),
        "flash_r3e_case_rows": len(explicit_r3e),
        "flash_r3e_explicit_not_run_rows": sum(row.get("execution") == "not_run" for row in explicit_r3e),
        "pro_case_rows": len(pro_all),
        "native_cells": len(native),
        "table_rows": len(table_rows),
        "network_or_model_calls_during_aggregation": 0,
        "state_guard_sha256": sha256_file(STATE),
        "source_hashes": {str(path.relative_to(ROOT)): sha256_file(path) for path in source_paths},
    }
    (OUT / "FORMAL_AUDIT.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    if errors:
        raise RuntimeError(errors)
    output_paths = [OUT / "FINAL_TABLE2.json", OUT / "FINAL_TABLE2.md",
                    OUT / "FINAL_LLM_PER_SEED_SUMMARY.jsonl", OUT / "FINAL_NATIVE_CELL_SUMMARY.jsonl",
                    OUT / "FLASH_R3E_CANONICAL_CASES_425.jsonl", OUT / "FLASH_R3E_EXPLICIT_DENOMINATOR_AUDIT.json",
                    OUT / "FORMAL_AUDIT.json"]
    freeze = {
        "schema": "r3e-final-table2-freeze-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "formal_audit_verdict": "PASS",
        "outputs": {str(path.relative_to(ROOT)): sha256_file(path) for path in output_paths},
        "source_hashes": audit["source_hashes"],
        "historical_artifacts_overwritten": False,
    }
    freeze_path = OUT / "FINAL_TABLE2_FREEZE.json"
    if freeze_path.exists():
        old = json.loads(freeze_path.read_text()); old.pop("created_at", None); freeze.pop("created_at", None)
        if old != freeze:
            raise RuntimeError("final Table 2 freeze drift")
    else:
        freeze_path.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return audit


def verify() -> None:
    freeze_path = OUT / "FINAL_TABLE2_FREEZE.json"
    freeze = json.loads(freeze_path.read_text())
    errors = []
    for group in ("outputs", "source_hashes"):
        for relative, expected in freeze[group].items():
            path = ROOT / relative
            if not path.is_file() or sha256_file(path) != expected:
                errors.append(f"{group}:{relative}")
    audit = json.loads((OUT / "FORMAL_AUDIT.json").read_text())
    if audit.get("verdict") != "PASS":
        errors.append("formal_audit_verdict")
    result = {"verdict": "PASS" if not errors else "FAIL", "errors": errors,
              "freeze": str(freeze_path.relative_to(ROOT))}
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise RuntimeError(errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("overlay-r3e", "finalize", "verify"))
    args = parser.parse_args()
    if args.command == "overlay-r3e":
        materialize_flash_r3e()
    elif args.command == "finalize":
        finalize()
    else:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
