"""Replay promoted-blue residual failures with a candidate next strategy.

This is a small closure experiment for the co-evolution narrative:
  Step 5: an adaptive red run against the promoted blue exposes residual failures.
  Step 6: replay those frozen failures with a candidate next strategy distilled
          from the residual pattern, without writing it into the official registry.

The script intentionally does not mutate .iso_semrepair/skills.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cirfix_adapter import parse_project  # noqa: E402
from functional_repair import apply_block, repair_one  # noqa: E402
from oracle_gate import judge  # noqa: E402
from skill_registry import make_strategy_recall  # noqa: E402

ROOT = Path(".")
CIRFIX = Path("/path/to/cirfix")
DEFAULT_SOURCE = ROOT / ".iso_semrepair" / "coevolve_struct_promoted_manual_20260625_222351"


_NEXT_STRATEGY = """
## 下一轮候选策略（由 promoted-blue residual failures 蒸馏；实验臂，未写入正式 registry）
1. 对循环移位/环形缓冲 residual：先判断语义是 circular shift 还是 zero-fill shift。
   若代码把第 i 位推到 i+1，最高位越界写必须通过收紧 loop bound 避免；最低位应来自原最高位
   （circular wrap），而不是来自次高位或常量 0。优先做成一处/两处最小修复，不重写整个 always 块。
2. 对边界比较 residual：若红方只把 inclusive threshold 改成 exclusive threshold（或反之），优先恢复
   原本的 one-line comparator 语义；不要引入新的地址选择、状态机或宽范围重构。重点检查等号是否丢失。
"""


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _select_poison(case: dict, red_dir: Path, design: str) -> tuple[Path | None, str | None]:
    for try_dir in sorted(red_dir.glob("try*")):
        candidates = [try_dir / f"{design}.v", try_dir / "buggy.v"]
        for poison in candidates:
            if not poison.exists():
                continue
            verdict = judge(
                case,
                str(poison),
                ROOT / ".iso_semrepair" / "adaptive_residual_reconstruct" / red_dir.parent.parent.name / design / try_dir.name,
                evidence_k=1,
            )
            if verdict.stage == "compare" and not verdict.ok:
                return poison, try_dir.name
    return None, None


def _case_from_record(source: Path, rec: dict) -> dict | None:
    design = rec["design"]
    rd = int(rec["round"])
    base = parse_project(CIRFIX / design / "project.toml")[0]
    poison, try_name = _select_poison(base, source / "work" / f"r{rd}" / design / "red", design)
    if not poison:
        return None
    return {
        "case_id": f"adaptive_r{rd}_{design}",
        "design_name": f"{design}__adaptive_r{rd}",
        "top_module": base["top_module"],
        "buggy_rtl": str(poison),
        "golden_rtl": base["golden_rtl"],
        "tb_sources": base["tb_sources"],
        "tb_output": base["tb_output"],
        "deps": base["deps"],
        "sim_timeout": base["sim_timeout"],
        "mutation_type": rec.get("red_mutation", ""),
        "source_round": rd,
        "source_design": design,
        "source_try": try_name,
        "source_skill": (rec.get("blue_preflight") or {}).get("skill_id"),
        "source_red_challenge": rec.get("red_challenge"),
    }


def collect_cases(source: Path, designs: set[str], limit: int) -> list[dict]:
    data = _load_json(source / "coevolve.json")
    cases = []
    for rec in data.get("detail", []):
        if rec.get("red_verdict") != "effective" or rec.get("blue_repaired"):
            continue
        if designs and rec.get("design") not in designs:
            continue
        case = _case_from_record(source, rec)
        if case:
            cases.append(case)
    return cases[:limit] if limit else cases


def next_strategy_recall(case: dict) -> str:
    base = make_strategy_recall()(case)
    return (base or "") + _NEXT_STRATEGY


def _deterministic_template_patch(case: dict, work_dir: Path) -> dict:
    """Apply a local candidate template distilled from residual failures.

    This is intentionally narrow and guarded by oracle_gate. A template hit is
    only counted if the patched RTL passes the same golden-vs-candidate judge.
    """
    lines = Path(case["buggy_rtl"]).read_text(errors="ignore").splitlines()

    # Circular shift residual: wrong wrap source plus out-of-range loop bound.
    for i, line in enumerate(lines):
        if "for" not in line or "i < 8" not in line:
            continue
        window = lines[i:min(len(lines), i + 6)]
        wrap_idx = next((j for j, ln in enumerate(window) if "op[0]" in ln and "op[6]" in ln), None)
        if wrap_idx is None:
            continue
        new_window = []
        for ln in window[:wrap_idx + 1]:
            ln = ln.replace("i < 8", "i < 7")
            ln = ln.replace("op[0] <= op[6]", "op[0] <= op[7]")
            new_window.append(ln)
        start = i + 1
        end = i + wrap_idx + 1
        return {
            "template_id": "next_dataflow_circular_shift_wrap_v1",
            "start_line": start,
            "end_line": end,
            "new_code": "\n".join(new_window),
        }

    # Boundary comparator residual: exclusive threshold should be inclusive.
    for i, line in enumerate(lines):
        if "refresh_cnt > CYCLES_BETWEEN_REFRESH" in line:
            return {
                "template_id": "next_boundary_inclusive_threshold_v1",
                "start_line": i + 1,
                "end_line": i + 1,
                "new_code": line.replace("refresh_cnt > CYCLES_BETWEEN_REFRESH",
                                         "refresh_cnt >= CYCLES_BETWEEN_REFRESH"),
            }

    return {"template_id": None, "miss": "no deterministic residual template matched"}


def run_deterministic_template(case: dict, work_dir: Path) -> dict:
    patch = _deterministic_template_patch(case, work_dir)
    rec = {k: v for k, v in patch.items() if k != "new_code"}
    if not patch.get("template_id"):
        rec["repaired"] = False
        return rec
    try:
        patched = apply_block(
            case["buggy_rtl"],
            int(patch["start_line"]),
            int(patch["end_line"]),
            patch["new_code"],
            work_dir / "patched_template.v",
        )
        verdict = judge(case, patched, work_dir / "post_template")
        rec["repaired"] = bool(verdict.ok)
        rec["patched_evidence"] = verdict.mismatch or verdict.err
        rec["judge_stage"] = verdict.stage
    except Exception as exc:  # noqa: BLE001
        rec["repaired"] = False
        rec["apply_error"] = str(exc)
    return rec


def run(args: argparse.Namespace) -> dict:
    source = Path(args.source)
    cases = collect_cases(source, set(args.design or []), args.limit)
    rows = []
    for case in cases:
        trials = []
        for rep in range(args.reps):
            if args.deterministic_template_only:
                res = run_deterministic_template(case, Path(args.work) / case["case_id"] / f"rep{rep}")
                trials.append({
                    "rep": rep,
                    "mode": "deterministic_template",
                    "repaired": bool(res.get("repaired")),
                    "template_id": res.get("template_id"),
                    "judge_stage": res.get("judge_stage"),
                    "apply_error": res.get("apply_error"),
                    "patched_evidence": res.get("patched_evidence"),
                    "miss": res.get("miss"),
                })
            else:
                res = repair_one(
                    case,
                    Path(args.work) / case["case_id"] / f"rep{rep}",
                    recall_fn=next_strategy_recall,
                    evidence_k=args.evidence_k,
                    n_candidates=args.n_candidates,
                    structured_evidence=args.structured_evidence,
                    preflight_registry=args.registry,
                    enable_template_preflight=args.template_preflight,
                )
                trials.append({
                    "rep": rep,
                    "mode": "llm_next_strategy",
                    "repaired": bool(res.get("repaired")),
                    "effective_evidence_k": res.get("effective_evidence_k"),
                    "effective_n_candidates": res.get("effective_n_candidates"),
                    "evidence_mode": res.get("evidence_mode"),
                    "repair_source": res.get("repair_source"),
                    "n_candidates_tried": res.get("n_candidates_tried"),
                    "patch_range": res.get("patch_range"),
                    "rationale": res.get("llm_rationale", ""),
                    "error": res.get("error"),
                    "apply_error": res.get("apply_error"),
                    "patched_evidence": res.get("patched_evidence"),
                })
        rate = sum(t["repaired"] for t in trials) / len(trials)
        row = {
            "case_id": case["case_id"],
            "design": case["source_design"],
            "mutation_type": case["mutation_type"],
            "source_round": case["source_round"],
            "source_try": case["source_try"],
            "promoted_blue_recorded_repaired": False,
            "next_strategy_success_rate": rate,
            "trials": trials,
        }
        rows.append(row)
        print(f"{row['case_id']}: promoted=0 next={rate:.3f}", flush=True)

    rates = [r["next_strategy_success_rate"] for r in rows]
    summary = {
        "purpose": "Adaptive residual closure: replay promoted-blue effective failures with candidate next strategy.",
        "source": str(source / "coevolve.json"),
        "registry": str(args.registry),
        "n_cases": len(rows),
        "reps": args.reps,
        "structured_evidence": args.structured_evidence,
        "deterministic_template_only": args.deterministic_template_only,
        "recorded_promoted_blue": {
            "fixed_cases_repaired": 0,
            "success_rate": 0.0,
            "source": "source coevolve detail rows where red_verdict=effective and blue_repaired=false",
        },
        "candidate_next_strategy": {
            "mean_success_rate": statistics.mean(rates) if rates else None,
            "fixed_cases_any_success": sum(r > 0 for r in rates),
            "fixed_cases_all_success": sum(r >= 1.0 for r in rates),
            "by_design": {
                d: {
                    "n": sum(1 for r in rows if r["design"] == d),
                    "any_success": sum(1 for r in rows if r["design"] == d and r["next_strategy_success_rate"] > 0),
                    "mean_success_rate": statistics.mean(
                        [r["next_strategy_success_rate"] for r in rows if r["design"] == d]
                    ),
                }
                for d in sorted({r["design"] for r in rows})
            },
            "mutation_counts": dict(Counter(r["mutation_type"] for r in rows)),
        },
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "out": str(out),
        "n_cases": summary["n_cases"],
        "next_mean": summary["candidate_next_strategy"]["mean_success_rate"],
        "next_any": summary["candidate_next_strategy"]["fixed_cases_any_success"],
    }, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--registry", default=str(ROOT / "configs" / "skills.json"))
    ap.add_argument("--work", default=str(ROOT / ".iso_semrepair" / "adaptive_residual_replay"))
    ap.add_argument("--out", default=str(ROOT / ".iso_semrepair" / "adaptive_residual_replay_20260626.json"))
    ap.add_argument("--design", action="append", default=None,
                    help="Restrict replay to one or more designs. Default: all effective unrepaired residuals.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--evidence-k", type=int, default=1)
    ap.add_argument("--n-candidates", type=int, default=3)
    ap.add_argument("--structured-evidence", action="store_true")
    ap.add_argument("--template-preflight", action="store_true")
    ap.add_argument("--deterministic-template-only", action="store_true",
                    help="Use only local residual templates + oracle gate; no external LLM call.")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
