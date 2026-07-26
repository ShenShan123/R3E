"""Shadow no-regression gate for adaptive residual deterministic templates.

The candidate templates are intentionally kept out of the official registry.
This gate checks:
  1. target residual replay still repairs the promoted-blue residual failures;
  2. non-target CirFix cases are not modified harmfully;
  3. Red-Fixed cases are not modified harmfully.

A template miss on a non-target case counts as safe. A template hit is safe only
if the patched RTL passes oracle_gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from adaptive_residual_replay import (  # noqa: E402
    DEFAULT_SOURCE,
    collect_cases,
    run_deterministic_template,
)

ROOT = Path(".")
S = ROOT / ".iso_semrepair"
TARGET_DESIGNS = {"lshift_reg", "sdram_controller"}


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _design_name(case: dict) -> str:
    return case["design_name"].split("__", 1)[0]


def _eval_case(case: dict, work: Path) -> dict:
    res = run_deterministic_template(case, work / case["design_name"])
    hit = bool(res.get("template_id"))
    repaired = bool(res.get("repaired"))
    return {
        "design_name": case["design_name"],
        "design": _design_name(case),
        "template_hit": hit,
        "template_id": res.get("template_id"),
        "safe": (not hit) or repaired,
        "repaired_if_hit": repaired if hit else None,
        "judge_stage": res.get("judge_stage"),
        "miss": res.get("miss"),
        "apply_error": res.get("apply_error"),
        "patched_evidence": res.get("patched_evidence"),
    }


def _summarize(rows: list[dict]) -> dict:
    hits = [r for r in rows if r["template_hit"]]
    harmful = [r for r in hits if not r["safe"]]
    return {
        "n_cases": len(rows),
        "template_hits": len(hits),
        "harmful_hits": len(harmful),
        "safe_rate": sum(r["safe"] for r in rows) / len(rows) if rows else None,
        "hit_repair_rate": (
            sum(bool(r["repaired_if_hit"]) for r in hits) / len(hits)
            if hits else None
        ),
    }


def run(args: argparse.Namespace) -> dict:
    work = Path(args.work)
    target_cases = collect_cases(Path(args.source), set(), 0)
    target_rows = [_eval_case(c, work / "target") for c in target_cases]

    cirfix = _load_jsonl(S / "survey" / "valid_functional.jsonl")
    non_target = [c for c in cirfix if _design_name(c) not in TARGET_DESIGNS][:args.n_cirfix]
    cirfix_rows = [_eval_case(c, work / "cirfix_non_target") for c in non_target]

    redfixed = _load_jsonl(S / "blue_abl" / "poison_pool.jsonl")
    if args.redfixed_exclude_target_designs:
        redfixed = [c for c in redfixed if _design_name(c) not in TARGET_DESIGNS]
    redfixed_rows = [_eval_case(c, work / "redfixed") for c in redfixed[:args.n_redfixed]]

    summary = {
        "purpose": "Shadow no-regression gate for candidate adaptive deterministic templates; official registry is not modified.",
        "source": str(Path(args.source) / "coevolve.json"),
        "target": _summarize(target_rows),
        "cirfix_non_target": _summarize(cirfix_rows),
        "redfixed_shadow": _summarize(redfixed_rows),
        "gate": {
            "target_all_repaired": all(r["template_hit"] and r["safe"] for r in target_rows),
            "cirfix_no_harmful_hit": _summarize(cirfix_rows)["harmful_hits"] == 0,
            "redfixed_no_harmful_hit": _summarize(redfixed_rows)["harmful_hits"] == 0,
        },
        "rows": {
            "target": target_rows,
            "cirfix_non_target": cirfix_rows,
            "redfixed_shadow": redfixed_rows,
        },
    }
    summary["gate"]["passed"] = all(summary["gate"].values())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "out": str(out),
        "gate": summary["gate"],
        "target": summary["target"],
        "cirfix_non_target": summary["cirfix_non_target"],
        "redfixed_shadow": summary["redfixed_shadow"],
    }, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--work", default=str(S / "adaptive_template_no_regression_gate"))
    ap.add_argument("--out", default=str(S / "adaptive_template_no_regression_gate_20260626.json"))
    ap.add_argument("--n-cirfix", type=int, default=20)
    ap.add_argument("--n-redfixed", type=int, default=12)
    ap.add_argument("--redfixed-exclude-target-designs", action="store_true")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
