"""Frozen replay for P5 prior-fail structural bugs.

This is intentionally not a new co-evolution curve.  It reconstructs the P5
bugs where the old blue strategy failed and asks whether the structural
promoted strategy can repair those fixed prior-fail cases.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cirfix_adapter import parse_project  # noqa: E402
from functional_repair import repair_one  # noqa: E402
from oracle_gate import judge  # noqa: E402
from skill_registry import make_strategy_recall  # noqa: E402

ROOT = Path(".")
CIRFIX = Path("/path/to/cirfix")
P5 = ROOT / ".iso_semrepair" / "p5_coevolve"
STRUCTURAL_DESIGNS = {"fsm_full", "lshift_reg"}


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _family(row: dict) -> str | None:
    design = row.get("design")
    text = " ".join(str(row.get(k, "")) for k in ("mutation_type", "reason", "upgrade_hint")).lower()
    if design == "lshift_reg" and any(x in text for x in ("data", "数据流", "移位", "方向", "index")):
        return "data_flow_error"
    if design == "fsm_full" and any(x in text for x in ("next_state", "state", "状态", "时序")):
        return "state_swap_error"
    return None


def _select_poison_path(base: dict, rd: int, design: str) -> tuple[Path | None, str | None]:
    red_dir = P5 / f"r{rd}" / design / "red"
    for poison in sorted(red_dir.glob(f"try*/{design}.v")):
        verdict = judge(
            base,
            str(poison),
            ROOT / ".iso_semrepair" / "prior_fail_reconstruct_check" / f"r{rd}_{design}_{poison.parent.name}",
            evidence_k=1,
        )
        if verdict.stage == "compare" and not verdict.ok:
            return poison, poison.parent.name
    return None, None


def _case_from_prior_fail(row: dict) -> dict | None:
    design = row["design"]
    rd = int(row["round"])
    fam = _family(row)
    if design not in STRUCTURAL_DESIGNS or not fam:
        return None
    base = parse_project(CIRFIX / design / "project.toml")[0]
    poison, selected_try = _select_poison_path(base, rd, design)
    if not poison:
        return None
    return {
        "case_id": f"p5_r{rd}_{design}_{fam}",
        "design_name": f"{design}__p5_r{rd}_{fam}",
        "top_module": base["top_module"],
        "buggy_rtl": str(poison),
        "golden_rtl": base["golden_rtl"],
        "tb_sources": base["tb_sources"],
        "tb_output": base["tb_output"],
        "deps": base["deps"],
        "sim_timeout": base["sim_timeout"],
        "mutation_type": "数据流反向" if fam == "data_flow_error" else "状态信号互换",
        "p5_round": rd,
        "p5_design": design,
        "p5_original_mutation_type": row.get("mutation_type"),
        "p5_selected_try": selected_try,
        "p5_verdict": row.get("verdict"),
        "p5_blue_repaired": bool(row.get("blue_repaired")),
        "family": fam,
    }


def collect_cases(limit: int = 0) -> list[dict]:
    rows = _load_jsonl(P5 / "red_mem.jsonl")
    cases = []
    seen = set()
    for row in rows:
        if row.get("verdict") != "effective" or row.get("blue_repaired"):
            continue
        case = _case_from_prior_fail(row)
        if not case:
            continue
        key = case["case_id"]
        if key in seen:
            continue
        seen.add(key)
        cases.append(case)
    return cases[:limit] if limit else cases


def run(args: argparse.Namespace) -> dict:
    cases = collect_cases(args.limit)
    if args.collect_only:
        summary = {
            "purpose": "P5 prior-fail structural replay manifest only; no LLM repair executed.",
            "source": str(P5 / "red_mem.jsonl"),
            "n_cases": len(cases),
            "cases": cases,
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"out": str(out), "n_cases": len(cases)}, ensure_ascii=False, indent=2))
        return summary
    recall = make_strategy_recall()
    rows = []
    for case in cases:
        trials = []
        for rep in range(args.reps):
            res = repair_one(
                case,
                Path(args.work) / case["case_id"] / f"rep{rep}",
                recall_fn=recall,
                evidence_k=args.evidence_k,
                n_candidates=args.n_candidates,
                structured_evidence=args.structured_evidence,
            )
            trials.append({
                "rep": rep,
                "repaired": bool(res.get("repaired")),
                "note": res.get("note"),
                "buggy_stage": res.get("buggy_stage"),
                "error": res.get("error"),
                "apply_error": res.get("apply_error"),
                "n_candidates_tried": res.get("n_candidates_tried"),
                "patch_range": res.get("patch_range"),
                "repair_source": res.get("repair_source"),
                "used_strategy": res.get("used_strategy"),
                "rationale": res.get("llm_rationale", ""),
            })
        rows.append({
            "case_id": case["case_id"],
            "design": case["p5_design"],
            "family": case["family"],
            "p5_round": case["p5_round"],
            "p5_original_mutation_type": case["p5_original_mutation_type"],
            "old_blue_repaired": False,
            "new_blue_success_rate": sum(t["repaired"] for t in trials) / len(trials),
            "trials": trials,
        })
        print(f"{rows[-1]['case_id']}: old=0 new={rows[-1]['new_blue_success_rate']:.3f}", flush=True)

    new_rates = [r["new_blue_success_rate"] for r in rows]
    summary = {
        "purpose": "P5 prior-fail frozen replay: old strategy failed; structural-promoted replay is tested on fixed cases.",
        "source": str(P5 / "red_mem.jsonl"),
        "n_cases": len(rows),
        "reps": args.reps,
        "evidence_k": args.evidence_k,
        "n_candidates": args.n_candidates,
        "structured_evidence": args.structured_evidence,
        "old_blue": {
            "fixed_cases_repaired": 0,
            "success_rate": 0.0,
            "source": "P5 co-evolution red_mem.jsonl, blue_repaired=false for all selected cases",
        },
        "structural_promoted_blue": {
            "mean_success_rate": statistics.mean(new_rates) if new_rates else None,
            "fixed_cases_any_success": sum(r["new_blue_success_rate"] > 0 for r in rows),
            "fixed_cases_all_success": sum(r["new_blue_success_rate"] >= 1.0 for r in rows),
        },
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "out": str(out),
        "n_cases": summary["n_cases"],
        "old_success_rate": summary["old_blue"]["success_rate"],
        "new_mean_success_rate": summary["structural_promoted_blue"]["mean_success_rate"],
        "new_any_success": summary["structural_promoted_blue"]["fixed_cases_any_success"],
    }, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(ROOT / ".iso_semrepair" / "prior_fail_structural_replay"))
    ap.add_argument("--out", default=str(ROOT / ".iso_semrepair" / "prior_fail_structural_replay.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--evidence-k", type=int, default=6)
    ap.add_argument("--n-candidates", type=int, default=3)
    ap.add_argument("--structured-evidence", action="store_true")
    ap.add_argument("--collect-only", action="store_true",
                    help="Only materialize the frozen prior-fail case manifest; do not call the LLM.")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
