#!/usr/bin/env python3
"""实验 B′: DeepSeek 自委员会讨论 + 第6执行器 — Blue-P5 失败 case 补盲.

对 Blue-P5 修复失败的 case, 用 5 个 lens agent 诊断 → 交叉评审 → 共识聚合
→ 第 6 DeepSeek 执行 → oracle_gate.

Two configs:
  Exec-NoDiscuss: 5 lens summaries + evidence → 1 executor (控制多一次调用)
  Discuss+Exec:   5 lens 诊断 → 交叉评审 → consensus → 1 executor (交流价值)

Reference: Population-Level Red-Blue Repair Evolution §B′
"""
from __future__ import annotations

import argparse, json, os, sys, time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microsurgeon_flow.backend_eco_oneshot import call_llm
from cirfix_adapter import parse_project
from functional_repair import apply_block
from oracle_gate import judge

CIRFIX = Path("/path/to/cirfix")

BLUE_LENSES = {
    "control-flow":  "Focus on control-flow errors: wrong branch targets, inverted conditions, case arm mismatches.",
    "state-update":  "Focus on state-update/timing errors: nonblocking vs blocking, next_state vs current_state swaps.",
    "datapath":      "Focus on datapath errors: wrong assignments, mux select inversion, bit-width mismatches.",
    "width-boundary":"Focus on width/boundary errors: off-by-one indices, wrong bit-slice ranges, shift amounts.",
    "reset-enable":  "Focus on reset/enable logic: missing reset conditions, wrong enable polarity, async vs sync.",
}
LENS_NAMES = list(BLUE_LENSES.keys())

DIAG_PROMPT = """你是 {lens} 修复专家。下面的 Verilog 模块能编译，但仿真输出与正确行为不符。

## Buggy RTL
{rtl}

## 仿真证据（mismatch）
{evidence}

## 任务
输出严格 JSON:
{{"suspected_location": "<行号或信号名>",
  "suspected_bug_family": "<off_by_one|constant_error|condition_error|operator_error|data_flow_error|state_swap_error|guard_error>",
  "patch_intent": "<一句话修复策略>",
  "confidence": <0-10>,
  "regression_risk": "<low|medium|high>"}}
"""

REVIEW_PROMPT = """你是 {lens} 修复评审。下面是你和其他 lens 专家的诊断摘要（匿名）。

## 其他专家诊断
{diagnoses}

## 你的原始诊断
{self_diag}

## Buggy RTL
{rtl}

## 仿真证据
{evidence}

## 任务
输出严格 JSON:
{{"agree_with_majority": <true/false>,
  "preferred_plan": "<最推荐的修复方案>",
  "disagreement_notes": "<分歧说明>",
  "confidence": <0-10>}}
"""

NO_DISCUSS_EXEC_PROMPT = """你是修复执行专家。下面是一个 Blue-P5 修复失败的 case。

## Buggy RTL
{rtl}

## 仿真证据
{evidence}

## 5 个 lens agent 的修复尝试
{attempts_summary}

## 约束
- 最小改动，只改导致 bug 的行
- 不引入新 module/endmodule
- {extra}

输出严格 JSON:
{{"start_line": <int>, "end_line": <int>, "new_code": "<替换代码,多行用 \\n>", "rationale": "<≤2句>"}}
"""

DISCUSS_EXEC_PROMPT = """你是修复执行专家。下面是一个 Blue-P5 修复失败的 case，经过 5 个 lens 专家交叉会诊。

## 会诊结果（各专家评审后的推荐方案）
{consensus}

## 各专家的详细意见
{discuss_details}

## Buggy RTL
{rtl}

## 仿真证据
{evidence}

## 约束
- 最小改动
- 不引入新 module/endmodule
{extra}
- 不引入新 module/endmodule
{extra}

输出严格 JSON:
{{"start_line": <int>, "end_line": <int>, "new_code": "<替换代码>", "rationale": "<≤2句>"}}
"""


def _numbered(text: str) -> str:
    return "\n".join(f"{i}: {ln}" for i, ln in enumerate(text.splitlines(), 1))


def _load_manifest(path: Path, limit: int = 0) -> list[dict]:
    cases = [json.loads(l) for l in open(path) if l.strip()]
    if limit > 0:
        cases = cases[:limit]
    return cases


def _build_fail_manifest(fail_names: list[str], manifest_path: Path) -> list[dict]:
    """Filter a manifest to only the named failing cases."""
    all_cases = _load_manifest(manifest_path)
    return [c for c in all_cases if c.get("design_name") in fail_names]


def _try_patch(case: dict, patch: dict, wd: Path) -> dict:
    """Apply patch + oracle_gate. Return {ok, evidence, error}."""
    try:
        patched = apply_block(case["buggy_rtl"],
                              int(patch.get("start_line", 1)),
                              int(patch.get("end_line", 1)),
                              patch.get("new_code", ""),
                              wd / "patched.v")
    except Exception as e:
        return {"ok": False, "error": f"apply: {e}"}
    post = judge(case, patched, wd / "post")
    return {"ok": post.ok, "evidence": post.mismatch or post.err or "",
            "stage": post.stage}


def run_experiment(fail_cases: list[dict], out_dir: Path, work_root: Path,
                   run_discuss: bool = True, dry_run: bool = False) -> dict:
    """Run B′ experiment on a list of Blue-P5-failed cases."""
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(work_root)
    per_case = []
    recovered = {"nodiscuss": 0, "discuss": 0, "all_five_fail_to_exec": {"nodiscuss": 0, "discuss": 0}}

    for ci, case in enumerate(fail_cases):
        name = case.get("design_name", f"case_{ci}")
        wd = work_root / name
        wd.mkdir(parents=True, exist_ok=True)
        print(f"\n[{ci+1}/{len(fail_cases)}] {name}", flush=True)

        # Pre-judge to get evidence
        pre = judge(case, case["buggy_rtl"], wd / "pre", evidence_k=3)
        if pre.ok:
            print(f"  SKIP: buggy already passes", flush=True)
            per_case.append({"design_name": name, "skipped": True})
            continue
        evidence = pre.mismatch or pre.err or "(no evidence)"
        rtl_text = Path(case["buggy_rtl"]).read_text(errors="ignore")

        # ── Step 1: 5 lens diagnoses ──
        diags = []
        for li, lens in enumerate(LENS_NAMES):
            if dry_run:
                diags.append({"suspected_location": f"line_{li+1}", "suspected_bug_family": "constant_error",
                              "patch_intent": "dry_run", "confidence": 5, "lens": lens})
                continue
            prompt = DIAG_PROMPT.format(lens=lens, rtl=_numbered(rtl_text), evidence=evidence[:600])
            print(f"  Diag {li+1}/5 ({lens}) ...", flush=True)
            d = call_llm(prompt)
            if isinstance(d, list):
                d = next((x for x in d if isinstance(x, dict)), {})
            if not isinstance(d, dict):
                d = {"llm_call_error": str(d)[:60]}
            d["lens"] = lens
            diags.append(d)
            if d.get("llm_call_error"):
                print(f"    FAILED: {d['llm_call_error'][:40]}", flush=True)
            else:
                print(f"    → {d.get('suspected_bug_family','?')} conf={d.get('confidence','?')} loc={d.get('suspected_location','?')}", flush=True)

        # Build 5-lens summary for NoDiscuss
        attempts_summary = "\n".join(
            f"Lens {d.get('lens','?')}: location={d.get('suspected_location','?')} "
            f"family={d.get('suspected_bug_family','?')} "
            f"intent={d.get('patch_intent','?')} conf={d.get('confidence','?')}"
            for d in diags
        )

        # ── Config A: Exec-NoDiscuss ──
        print(f"  [NoDiscuss] Executor ...", flush=True)
        if dry_run:
            nodiscuss_patch = {"start_line": 1, "end_line": 2, "new_code": "// nodiscuss", "rationale": "dry"}
        else:
            prompt = NO_DISCUSS_EXEC_PROMPT.format(
                rtl=_numbered(rtl_text), evidence=evidence[:400],
                attempts_summary=attempts_summary,
                extra="参考 5 个 lens 的失败尝试，避开已知的失败 patch 路径")
            nodiscuss_patch = call_llm(prompt)
            if isinstance(nodiscuss_patch, list):
                nodiscuss_patch = next((x for x in nodiscuss_patch if isinstance(x, dict)), {})

        nd_result = _try_patch(case, nodiscuss_patch, wd / "nodiscuss")
        nd_ok = nd_result.get("ok", False)
        if nd_ok:
            recovered["nodiscuss"] += 1
            recovered["all_five_fail_to_exec"]["nodiscuss"] += 1
        print(f"    → {'RECOVERED' if nd_ok else 'FAILED'}", flush=True)

        # ── Config B: Discuss+Exec ──
        discuss_ok = False
        consensus_plan = {}
        if run_discuss:
            # Step 2: Cross review
            if not dry_run:
                reviews = []
                for li, (lens, diag) in enumerate(zip(LENS_NAMES, diags)):
                    prompt = REVIEW_PROMPT.format(
                        lens=lens,
                        diagnoses=attempts_summary,
                        self_diag=json.dumps(diag, ensure_ascii=False),
                        rtl=_numbered(rtl_text), evidence=evidence[:400])
                    print(f"  Review {li+1}/5 ({lens}) ...", flush=True)
                    r = call_llm(prompt)
                    if isinstance(r, list):
                        r = next((x for x in r if isinstance(x, dict)), {})
                    if not isinstance(r, dict):
                        r = {"llm_call_error": str(r)[:60]}
                    r["lens"] = lens
                    reviews.append(r)

                # Step 3: Consensus aggregation — USE REVIEW output, not raw diags
                # Extract preferred plans from reviews (not the original diags)
                preferred_plans = [r.get("preferred_plan", "") for r in reviews if r.get("preferred_plan")]
                disagreements = [r.get("disagreement_notes", "") for r in reviews if r.get("disagreement_notes")]
                agree_count = sum(1 for r in reviews if r.get("agree_with_majority", False))

                # Consensus: use reviews' preferred_plan as the refined diagnosis,
                # NOT the raw majority vote on original diag locations
                consensus_text = "\n".join(
                    f"Lens {r.get('lens','?')}[review]: plan={r.get('preferred_plan','?')} "
                    f"agree={r.get('agree_with_majority','?')} conf={r.get('confidence','?')}"
                    for r in reviews
                )
                disagreement_text = "\n".join(
                    f"  [{r.get('lens','?')}] {r.get('disagreement_notes','')}"
                    for r in reviews if r.get('disagreement_notes', '').strip()
                )

                consensus_plan = {
                    "preferred_plans": preferred_plans[:3],
                    "agreement_count": f"{agree_count}/{len(reviews)}",
                    "disagreements": disagreements[:3] if disagreements else ["(无分歧)"],
                    "n_advisors": len(reviews),
                }

                failed_attempts_summary = "\n".join(
                    f"  [{r.get('lens','?')}] agree={r.get('agree_with_majority','?')} "
                    f"plan={r.get('preferred_plan','?')} (conf={r.get('confidence','?')})"
                    for r in reviews
                )

                print(f"  [Discuss] Executor (with {agree_count}/{len(reviews)} agree) ...", flush=True)
                exec_prompt = DISCUSS_EXEC_PROMPT.format(
                    consensus=consensus_text,
                    discuss_details=failed_attempts_summary,
                    rtl=_numbered(rtl_text), evidence=evidence[:400],
                    extra=f"会诊{agree_count}/{len(reviews)}同意。分歧: {disagreement_text[:300] if disagreement_text else '无'}。优先参考高同意度方案，但结合分歧独立判断最佳修复位置。")
                discuss_patch = call_llm(exec_prompt)
                if isinstance(discuss_patch, list):
                    discuss_patch = next((x for x in discuss_patch if isinstance(x, dict)), {})
            else:
                discuss_patch = {"start_line": 1, "end_line": 2, "new_code": "// discuss", "rationale": "dry"}

            ds_result = _try_patch(case, discuss_patch, wd / "discuss")
            discuss_ok = ds_result.get("ok", False)
            if discuss_ok:
                recovered["discuss"] += 1
                recovered["all_five_fail_to_exec"]["discuss"] += 1
            print(f"    → {'RECOVERED' if discuss_ok else 'FAILED'}", flush=True)

        entry = {"design_name": name,
                 "nodiscuss": {"recovered": nd_ok, **nd_result},
                 "discuss": {"recovered": discuss_ok, **consensus_plan}}
        if not dry_run and run_discuss:
            entry["consensus"] = consensus_plan
        per_case.append(entry)

    # Summary
    totals = {
        "total_cases": len(fail_cases),
        "nodiscuss_recovered": recovered["nodiscuss"],
        "discuss_recovered": recovered["discuss"],
        "nodiscuss_rate": round(recovered["nodiscuss"] / max(len(fail_cases), 1), 4),
        "discuss_rate": round(recovered["discuss"] / max(len(fail_cases), 1), 4),
        "discuss_gain": recovered["discuss"] - recovered["nodiscuss"],
        "all_five_fail_to_exec": recovered["all_five_fail_to_exec"],
    }
    out = {"totals": totals, "per_case": per_case}
    (out_dir / "bprime_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n=== B′ Results ===")
    print(f"  Exec-NoDiscuss: {totals['nodiscuss_recovered']}/{totals['total_cases']} = {totals['nodiscuss_rate']*100:.1f}%")
    print(f"  Discuss+Exec:   {totals['discuss_recovered']}/{totals['total_cases']} = {totals['discuss_rate']*100:.1f}%")
    print(f"  Discuss Gain:   {totals['discuss_gain']:+d}")
    return out


def main():
    ap = argparse.ArgumentParser(description="B′: DeepSeek Self-Committee")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-discuss", action="store_true", help="Only run Exec-NoDiscuss")
    args = ap.parse_args()

    OUT = Path(args.out)
    WORK = Path(args.work)

    ROOT = Path(".")
    S = ROOT / ".iso_semrepair"

    # CirFix-Fail11
    cirfix_manifest = S / "survey/valid_functional.jsonl"
    cirfix_fails = json.load(open(S / "blue_cirfix/p5/Blue-P5.json"))
    cfail_names = [c["design_name"] for c in cirfix_fails["per_case"] if not c["repaired"]]
    cfail_cases = [json.loads(l) for l in open(cirfix_manifest) if l.strip()]
    cfail_cases = [c for c in cfail_cases if c.get("design_name") in cfail_names]

    # Red24-Fail2
    red24_fails = json.load(open(S / "blue_red24/p5/Blue-P5.json"))
    rf_names = [c["design_name"] for c in red24_fails["per_case"] if not c["repaired"]]
    rf_cases = [json.loads(l) for l in open(S / "heldout_red24.jsonl") if l.strip()]
    rf_cases = [c for c in rf_cases if c.get("design_name") in rf_names]

    all_cases = cfail_cases + rf_cases
    print(f"B′ Self-Committee: {len(cfail_cases)} CirFix-Fail11 + {len(rf_cases)} Red24-Fail2 = {len(all_cases)} cases")

    run_experiment(all_cases, OUT / "results", WORK,
                   run_discuss=not args.no_discuss, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
