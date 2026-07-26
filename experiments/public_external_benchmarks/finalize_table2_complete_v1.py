#!/usr/bin/env python3
"""Freeze the complete Table 2, including the matched constrained diagnostics.

This is an offline-only composition step.  It never invokes a model or an
evaluator and it refuses to overwrite a non-identical frozen output.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from table2_common import sha256_file


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".cross_benchmark_r3e_202607/table2_final_20260723_v5_complete"
BASE = ROOT / ".cross_benchmark_r3e_202607/table2_final_20260723_v2"
MATCHED = ROOT / ".cross_benchmark_r3e_202607/table2_constrained_baselines_20260722_v2_supplement_v2"
BENCHMARKS = ("cirfix39", "literature32", "strider14")


def frozen_write(path: Path, payload: str) -> None:
    if path.exists() and path.read_text() != payload:
        raise RuntimeError(f"refusing to alter frozen output: {path}")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def fmt2(value: float) -> str:
    """Use conventional paper rounding rather than binary/banker's rounding."""
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def matched_cells(method: str, metric: str) -> dict[str, str]:
    aggregate = load_json(MATCHED / "AGGREGATE.json")
    rows = aggregate["summary"]
    selected = {(row["method"], row["benchmark"]): row for row in rows}
    cells: dict[str, str] = {}
    for benchmark in BENCHMARKS:
        row = selected[(method, benchmark)]
        if metric == "pass_at_1":
            mean = row["pass_at_1_mean_pct"]
            sd = row["pass_at_1_sample_sd_pct"]
        else:
            mean = row["within_3_mean_pct"]
            sd = row["within_3_sample_sd_pct"]
        cells[benchmark] = f"{fmt2(mean)}±{fmt2(sd)}"
    return cells


def finalize() -> int:
    base_audit = load_json(BASE / "FORMAL_AUDIT.json")
    matched_audit = load_json(MATCHED / "FORMAL_AUDIT.json")
    if base_audit.get("verdict") != "PASS" or matched_audit.get("verdict") != "PASS":
        raise RuntimeError("a required source audit is not PASS")

    base = load_json(BASE / "FINAL_TABLE2.json")
    by_key = {(row["method"], row["search_protocol"]): row for row in base["rows"]}
    native = [row for row in base["rows"] if row["section"] == "Traditional RTL APR"]
    flash_r3e = by_key[("R³E", "DeepSeek-v4-flash; ≤3 verified revisions; within-3")]
    pro_direct = by_key[("Direct LLM", "DeepSeek-v4-Pro; 1 call; pass@1")]
    pro_r3e = by_key[("R³E", "DeepSeek-v4-Pro; ≤3 verified revisions; within-3")]

    direct_pass1 = matched_cells("direct_bo3_constrained", "pass_at_1")
    direct_within3 = matched_cells("direct_bo3_constrained", "within_3")
    feedback_pass1 = matched_cells("feedback_constrained", "pass_at_1")
    feedback_within3 = matched_cells("feedback_constrained", "within_3")
    if direct_pass1 != feedback_pass1:
        raise RuntimeError("shared candidate-0 invariant failed")

    rows = native + [
        {"section": "Budget-matched constrained diagnostics", "method": "Direct Best-of-3 constrained",
         "search_protocol": "pass@1", "cells": direct_pass1},
        {"section": "Budget-matched constrained diagnostics", "method": "Direct Best-of-3 constrained",
         "search_protocol": "within 3", "cells": direct_within3},
        {"section": "Budget-matched constrained diagnostics", "method": "Feedback constrained",
         "search_protocol": "pass@1", "cells": feedback_pass1},
        {"section": "Budget-matched constrained diagnostics", "method": "Feedback constrained",
         "search_protocol": "within 3", "cells": feedback_within3},
        {"section": "Same-backbone comparison", "method": "Direct LLM (flash)",
         "search_protocol": "constrained bounded patch; 1 call (pass@1)", "cells": direct_pass1,
         "reuses": "Direct Best-of-3 constrained candidate-0"},
        {**flash_r3e, "section": "Same-backbone comparison", "method": "R³E (flash)",
         "search_protocol": "frozen strategy + evaluator feedback; ≤3 verified revisions"},
        {**pro_direct, "section": "Backbone robustness", "method": "Direct LLM (Pro)",
         "search_protocol": "1 call; pass@1"},
        {**pro_r3e, "section": "Backbone robustness", "method": "R³E (Pro)",
         "search_protocol": "bounded patch; ≤3 verified revisions"},
    ]
    # The caption already declares percent; strip duplicated percent signs from
    # inherited rows so every cell follows the same representation.
    for row in rows:
        row["cells"] = {key: value.replace("%", "") for key, value in row["cells"].items()}

    table = {
        "schema": "r3e-final-table2-complete-v1",
        "title": "Cross-benchmark RTL functional repair rate (%)",
        "fixed_denominators": {"cirfix39": 39, "literature32": 32, "strider14": 14},
        "stochastic_reporting": "five-seed mean ± sample SD in percentage points",
        "native_reporting": "CirFix cross-benchmark cells use seeds 101/202/303; deterministic methods run once",
        "literature_dagger": "four SHA3 cases per seed were not run and are counted unrepaired on the frozen 32-case denominator; native terminal cases are also retained as unrepaired",
        "shared_candidate_zero": "Direct Best-of-3 constrained pass@1, Feedback constrained pass@1, and Direct LLM (flash) are the same frozen candidate-0 observations, not three independent runs",
        "rows": rows,
    }
    frozen_write(OUT / "FINAL_TABLE2_COMPLETE.json", json.dumps(table, indent=2, ensure_ascii=False, sort_keys=True) + "\n")

    lines = [
        "# Final complete Table 2", "",
        "| Method | Search protocol | CirFix-39 ↑ | Literature-32† ↑ | Strider-14 ↑ |",
        "|---|---|---:|---:|---:|",
    ]
    section = None
    for row in rows:
        if section != row["section"]:
            section = row["section"]
            lines.append(f"| **{section}** |  |  |  |  |")
        cells = row["cells"]
        label = f"**{row['method']}**" if row["method"].startswith("R³E") else row["method"]
        lines.append(f"| {label} | {row['search_protocol']} | {cells['cirfix39']} | {cells['literature32']} | {cells['strider14']} |")
    lines += [
        "", "All stochastic cells are five-seed mean ± sample SD (percentage points).",
        "† Four SHA3 cases per seed were not run and are counted as failures on the frozen Literature-32 denominator; native terminal cases are likewise not removed.",
        "The three Flash pass@1 rows with identical values reuse the same frozen candidate-0 observations.", "",
    ]
    frozen_write(OUT / "FINAL_TABLE2_COMPLETE.md", "\n".join(lines))

    source_paths = [
        Path(__file__).resolve(), BASE / "FINAL_TABLE2.json", BASE / "FORMAL_AUDIT.json",
        MATCHED / "AGGREGATE.json", MATCHED / "CANONICAL_CASES.jsonl",
        MATCHED / "FORMAL_AUDIT.json", MATCHED / "REPLACEMENT_LEDGER.jsonl",
    ]
    errors = []
    if len(rows) != 11:
        errors.append(f"table row cardinality: {len(rows)}")
    if table["fixed_denominators"] != {"cirfix39": 39, "literature32": 32, "strider14": 14}:
        errors.append("denominator drift")
    audit = {
        "schema": "r3e-final-table2-complete-formal-audit-v1",
        "verdict": "PASS" if not errors else "FAIL",
        "errors": errors,
        "table_rows": len(rows),
        "network_or_model_calls_during_composition": 0,
        "shared_candidate_zero_verified": direct_pass1 == feedback_pass1,
        "source_audit_verdicts": {"base_final_v2": base_audit["verdict"], "constrained_supplement_v2": matched_audit["verdict"]},
        "source_hashes": {str(path.relative_to(ROOT)): sha256_file(path) for path in source_paths},
    }
    frozen_write(OUT / "FORMAL_AUDIT.json", json.dumps(audit, indent=2, sort_keys=True) + "\n")
    if errors:
        raise RuntimeError(errors)
    output_paths = [OUT / "FINAL_TABLE2_COMPLETE.json", OUT / "FINAL_TABLE2_COMPLETE.md", OUT / "FORMAL_AUDIT.json"]
    freeze = {
        "schema": "r3e-final-table2-complete-freeze-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "historical_artifacts_overwritten": False,
        "outputs": {str(path.relative_to(ROOT)): sha256_file(path) for path in output_paths},
        "source_hashes": audit["source_hashes"],
    }
    freeze_path = OUT / "FINAL_TABLE2_COMPLETE_FREEZE.json"
    if freeze_path.exists():
        freeze["created_at"] = load_json(freeze_path)["created_at"]
    frozen_write(freeze_path, json.dumps(freeze, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0


def verify() -> int:
    freeze_path = OUT / "FINAL_TABLE2_COMPLETE_FREEZE.json"
    freeze = load_json(freeze_path)
    errors = []
    for group in ("outputs", "source_hashes"):
        for relative, expected in freeze[group].items():
            path = ROOT / relative
            if not path.is_file() or sha256_file(path) != expected:
                errors.append(f"{group}:{relative}")
    if load_json(OUT / "FORMAL_AUDIT.json").get("verdict") != "PASS":
        errors.append("formal_audit_verdict")
    result = {"verdict": "PASS" if not errors else "FAIL", "errors": errors,
              "freeze": str(freeze_path.relative_to(ROOT))}
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise RuntimeError(errors)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", choices=("finalize", "verify"), default="finalize")
    args = parser.parse_args()
    return verify() if args.command == "verify" else finalize()


if __name__ == "__main__":
    raise SystemExit(main())
