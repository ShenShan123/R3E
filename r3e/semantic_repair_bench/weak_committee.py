#!/usr/bin/env python3
"""实验 B: Weak Committee — 弱模型委员会讨论 + 第 6 agent 执行.

Flow per case:
  Step 1: 5 advisors → independent diagnosis
  Step 2: 5 advisors → cross review (see each other's anonymized diagnoses)
  Step 3: deterministic consensus aggregation
  Step 4: executor → apply patch per consensus plan
  Step 5: oracle gate

Reference: Population-Level Red-Blue Repair Evolution §B1–B6
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
from oracle_gate import judge, SimOutcome

# ── weak model registry ─────────────────────────────────────────────────
# Model identifiers are examples only. Provider routes and allowed hosts must
# be supplied explicitly through environment variables.
ADVISOR_MODELS = [
    ("glm-5.2", "vector"),           # 9s  json OK
    ("MiniMax-M3", "vector"),        # 8s  json OK
    ("qwen3.7-max", "vector"),       # 16s json OK
    ("kimi-k2.7-code", "vector"),    # 4s  json OK
    ("deepseek-v4-flash", "vector"), # 14s json OK
]

EXECUTOR_MODEL = ("deepseek-v4-flash", "vector")

# ── prompts ─────────────────────────────────────────────────────────────
DIAG_PROMPT = """你是硬件 RTL 功能 bug 诊断专家。下面的 Verilog 模块能编译，但 testbench 输出与正确行为不符。

## Buggy RTL（带行号）
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

REVIEW_PROMPT = """你是硬件 RTL 修复评审专家。下面是其他诊断专家的结论（匿名）。

## 所有专家诊断
{diagnoses}

## Buggy RTL
{rtl}

## 仿真证据
{evidence}

## 任务
输出严格 JSON:
{{"agree_with_majority": <true/false>,
  "preferred_plan": "<你最推荐的修复方案>",
  "disagreement_notes": "<分歧说明>",
  "risk_warning": "<如果有风险>"}}
"""

EXEC_PROMPT = """你是硬件 RTL 修复执行专家。根据共识方案输出 patch。

## 共识方案
{safest_plan}

## Buggy RTL（带行号）
{rtl}

## 约束
- 最小改动，只改导致 bug 的行
- 不引入新 module/endmodule
- {extra}

输出严格 JSON:
{{"start_line": <int>, "end_line": <int>, "new_code": "<替换代码,多行用 \\n>", "rationale": "<≤2句>"}}
"""


def _numbered(text: str) -> str:
    return "\n".join(f"{i}: {ln}" for i, ln in enumerate(text.splitlines(), 1))


_VECTOR_KEY: str | None = None

def _read_vector_key() -> str:
    """Read the optional VectorEngine API key from the environment."""
    global _VECTOR_KEY
    if _VECTOR_KEY:
        return _VECTOR_KEY
    _VECTOR_KEY = os.environ.get("VECTOR_API_KEY") or os.environ.get("LLM_API_KEY", "")
    return _VECTOR_KEY


def _call_model(prompt: str, model_id: str, provider: str) -> dict:
    """Call a specific model by temporarily switching env vars."""
    env = {}
    for k in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL",
              "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        env[k] = os.environ.get(k, "")

    vector_key = _read_vector_key()
    vector_base_url = os.environ.get("VECTOR_BASE_URL", "")

    if provider == "vector":
        if not vector_base_url:
            return {"llm_call_error": "VECTOR_BASE_URL is not configured"}
        os.environ["LLM_MODEL"] = model_id
        os.environ["LLM_BASE_URL"] = vector_base_url
        vector_key = _read_vector_key()
        if vector_key:
            os.environ["LLM_API_KEY"] = vector_key
    elif provider == "glm52":
        if not vector_base_url:
            return {"llm_call_error": "VECTOR_BASE_URL is not configured"}
        os.environ["LLM_MODEL"] = model_id
        os.environ["LLM_BASE_URL"] = vector_base_url
        os.environ["LLM_API_KEY"] = vector_key
        os.environ["OPENAI_API_KEY"] = vector_key

    try:
        result = call_llm(prompt)
    finally:
        for k, v in env.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    if isinstance(result, list):
        result = next((x for x in result if isinstance(x, dict)), None)
    if not isinstance(result, dict):
        return {"llm_call_error": str(result)[:60]}
    return result


def run_committee(cases: list[dict], work_root: Path, out_dir: Path,
                  dry_run: bool = False) -> dict:
    """Run Weak Committee on a list of repair cases."""
    work_root = Path(work_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    per_case = []
    totals = {"total": len(cases), "repaired": 0, "single_baselines": {}}

    for ci, case in enumerate(cases):
        name = case.get("design_name", f"case_{ci}")
        wd = work_root / name
        wd.mkdir(parents=True, exist_ok=True)
        print(f"\n[{ci+1}/{len(cases)}] {name}", flush=True)

        # ── Pre-judge ──
        pre = judge(case, case["buggy_rtl"], wd / "pre", evidence_k=3)
        if pre.ok:
            per_case.append({"design_name": name, "repaired": None,
                             "note": "buggy already passes"})
            continue
        evidence = pre.mismatch or pre.err or "(no evidence)"
        rtl_text = Path(case["buggy_rtl"]).read_text(errors="ignore")

        # ── Step 1: Independent Diagnosis ──
        diags = []
        for midx, (model_id, provider) in enumerate(ADVISOR_MODELS):
            if dry_run:
                diags.append({"suspected_location": f"line_{midx+1}",
                              "suspected_bug_family": "constant_error",
                              "patch_intent": "dry_run", "confidence": 5,
                              "regression_risk": "low", "model": model_id})
                continue
            print(f"  Diagnosis {midx+1}/{5} ({model_id}) ...", flush=True)
            d = _call_model(
                DIAG_PROMPT.format(rtl=_numbered(rtl_text), evidence=evidence[:600]),
                model_id, provider)
            d["model"] = model_id
            diags.append(d)
            if d.get("llm_call_error"):
                print(f"    FAILED: {d['llm_call_error'][:40]}", flush=True)

        # ── Step 2: Cross Review ──
        diag_summary = "\n".join(
            f"Expert {i}: location={d.get('suspected_location','?')} "
            f"family={d.get('suspected_bug_family','?')} "
            f"confidence={d.get('confidence','?')} intent={d.get('patch_intent','?')}"
            for i, d in enumerate(diags)
        )
        reviews = []
        for midx, (model_id, provider) in enumerate(ADVISOR_MODELS):
            if dry_run:
                reviews.append({"agree_with_majority": True,
                                "preferred_plan": "dry_run", "model": model_id})
                continue
            print(f"  Review {midx+1}/{5} ({model_id}) ...", flush=True)
            r = _call_model(
                REVIEW_PROMPT.format(diagnoses=diag_summary,
                                     rtl=_numbered(rtl_text),
                                     evidence=evidence[:400]),
                model_id, provider)
            r["model"] = model_id
            reviews.append(r)

        # ── Step 3: Consensus Aggregation ──
        locations = [d.get("suspected_location", "") for d in diags]
        families = [d.get("suspected_bug_family", "") for d in diags]
        intents = [d.get("patch_intent", "") for d in diags]

        # Majority vote
        top_loc = Counter(locations).most_common(1)[0][0] if locations else ""
        top_fam = Counter(families).most_common(1)[0][0] if families else ""
        top_intent = Counter(intents).most_common(1)[0][0] if intents else ""
        avg_conf = sum(float(d.get("confidence", 5) or 0) for d in diags) / max(len(diags), 1)

        # Safest plan: prefer higher-family-specific hints
        safest_plan = f"Location: {top_loc}, Family: {top_fam}, Intent: {top_intent}"
        consensus = {
            "majority_location": top_loc, "majority_family": top_fam,
            "safest_plan": safest_plan, "avg_confidence": round(avg_conf, 1),
            "n_advisors": len(diags),
        }

        # ── Step 4: Executor ──
        if dry_run:
            patch = {"start_line": 1, "end_line": 1,
                     "new_code": "// dry_run", "rationale": "dry_run"}
        else:
            model_id, provider = EXECUTOR_MODEL
            print(f"  Executor ({model_id}) ...", flush=True)
            extra = "结合上述 consensus 方案定位最小修复"
            exec_prompt = EXEC_PROMPT.format(
                safest_plan=safest_plan, rtl=_numbered(rtl_text), extra=extra)
            patch = _call_model(exec_prompt, model_id, provider)

        # ── Step 5: Apply + Gate ──
        result = {"design_name": name, "consensus": consensus,
                  "diags": [{"model": d.get("model", ""),
                             "family": d.get("suspected_bug_family", ""),
                             "confidence": d.get("confidence", ""),
                             "error": d.get("llm_call_error", "")}
                            for d in diags],
                  "patch": patch}

        if isinstance(patch, dict) and not patch.get("llm_call_error"):
            try:
                patched = apply_block(case["buggy_rtl"],
                                      int(patch.get("start_line", 1)),
                                      int(patch.get("end_line", 1)),
                                      patch.get("new_code", ""),
                                      wd / "patched.v")
                post = judge(case, patched, wd / "post")
                result["repaired"] = post.ok
                result["patched_evidence"] = post.mismatch or post.err or ""
                print(f"  → {'修复' if post.ok else '未修'} (gate)", flush=True)
            except Exception as e:
                result["repaired"] = False
                result["apply_error"] = str(e)
                print(f"  → apply失败: {e}", flush=True)
        else:
            result["repaired"] = False
            result["error"] = "executor failed"

        if result.get("repaired"):
            totals["repaired"] += 1
        per_case.append(result)

    totals["rate"] = round(totals["repaired"] / max(totals["total"], 1), 4)
    out = {"totals": totals, "per_case": per_case}
    (out_dir / "weak_committee.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n=== Weak Committee: {totals['repaired']}/{totals['total']} = {totals['rate']*100:.1f}% ===")
    return out


def main():
    ap = argparse.ArgumentParser(description="Weak Committee experiment")
    ap.add_argument("--manifest", required=True, help="JSONL manifest of cases")
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-cases", type=int, default=0, help="Limit cases")
    args = ap.parse_args()

    cases = [json.loads(l) for l in open(args.manifest) if l.strip()]
    if args.max_cases > 0:
        cases = cases[:args.max_cases]
    print(f"Weak Committee: {len(cases)} cases", flush=True)

    run_committee(cases, args.work, args.out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
