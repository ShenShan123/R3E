#!/usr/bin/env python3
"""R5-Pipe: 5 角色红方流水线 — correctness-guided co-evolution.

Pipeline (per design per round):
  Red-1 Generator  → 生成 atomic poison
  Red-2 Variant    → 同族变体强化（更难被发现）
  Red-3 Critic     → 去重 + family 标注 + novelty score
  Red-4 Combiner   → 组合两个兼容 atomic probes → composite
  Red-5 Judge      → 预测边界风险，排名输出

Enforced constraints:
  - Atomic: ≤1 bug family, single edit site
  - Composite: same design, ≤2 edit sites, different families,
               compile pass + oracle fail
  - All probes must pass admission: golden PASS ∧ buggy compile ∧ buggy oracle FAIL
"""
from __future__ import annotations

import argparse, hashlib, json, os, sys, time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microsurgeon_flow.backend_eco_oneshot import call_llm  # noqa: E402
from cirfix_adapter import parse_project                                  # noqa: E402
from functional_repair import apply_block, repair_one                    # noqa: E402
from oracle_gate import judge                                             # noqa: E402
from red_critic import critique, generalize                               # noqa: E402
import red_memory                                                         # noqa: E402

# ── constants ──────────────────────────────────────────────────────────
BUG_FAMILIES = [
    "off_by_one", "wrong_constant", "current_vs_next_state",
    "state_target_swap", "sticky_valid", "dataflow_substitution",
    "width_boundary", "reset_enable_misuse",
    # structural families from P5 co-evolution
    "数据流反向(移位方向<< vs >>, 或赋值源与目标互换)",
    "状态信号互换(当前态 vs 下一态, 利用非阻塞延迟)",
]

# ── shared helpers ─────────────────────────────────────────────────────
def _numbered(text: str) -> str:
    return "\n".join(f"{i}: {ln}" for i, ln in enumerate(text.splitlines(), 1))


def _fingerprint(design: str, mutation_type: str, rationale: str, rng=None) -> str:
    raw = f"{design}|{mutation_type}|{rationale}|{rng or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _admission_gate(case: dict, buggy_path: str, wd: Path) -> dict | None:
    """三闸: golden PASS ∧ buggy compile ∧ buggy oracle FAIL."""
    wd = Path(wd)
    g = judge(case, buggy_path, wd / "gate")
    if g.stage == "cand_sim":
        return {"passed": False, "stage": "compile_fail", "error": g.err}
    if g.ok:
        return {"passed": False, "stage": "no_mismatch", "error": "candidate == golden"}
    return {"passed": True, "evidence": g.mismatch or g.err, "stage": "admitted"}


# ══════════════════════════════════════════════════════════════════════
# Red-1: Generator — 生成 atomic poison
# ══════════════════════════════════════════════════════════════════════
_GENERATOR_PROMPT = """你是红队的种子毒生成器。对下面的正确 RTL 注入一处语义 bug。

## 约束
1. 只注入语义/功能 bug，保持可编译。
2. 只改一处（单行/相邻最多 3 行）。
3. 候选 bug 类型: {families}
4. 不能加 module/endmodule，不能改 testbench。
{memory}

## Golden RTL
{rtl}

输出严格 JSON:
{{"start_line": <int>, "end_line": <int>, "new_code": "<多行用 \\n>", "mutation_type": "<类型>", "rationale": "<为什么这样改会导致功能错但仍可编译>"}}
"""

def red1_generate(golden_rtl: str, work_dir: Path, red_memory_ctx: str = "",
                  preferred_family: str | None = None) -> dict:
    """生成一个 atomic poison."""
    rtl = Path(golden_rtl).read_text(errors="ignore")
    families_str = preferred_family or ", ".join(BUG_FAMILIES[:6])
    mem = f"\n## 红队记忆\n{red_memory_ctx}" if red_memory_ctx else ""
    prompt = _GENERATOR_PROMPT.format(rtl=_numbered(rtl), families=families_str, memory=mem)

    out = call_llm(prompt)
    if isinstance(out, list):
        out = next((x for x in out if isinstance(x, dict)), None) or {"llm_call_error": "list"}
    if not isinstance(out, dict) or "llm_call_error" in out:
        return {"ok": False, "error": str(out)}

    try:
        buggy = apply_block(golden_rtl, int(out["start_line"]), int(out["end_line"]),
                            out.get("new_code", ""), Path(work_dir) / "buggy.v")
    except Exception as e:
        return {"ok": False, "error": f"apply_failed: {e}"}

    return {"ok": True, "buggy_path": str(buggy),
            "mutation_type": out.get("mutation_type", ""),
            "rationale": out.get("rationale", ""),
            "range": [out.get("start_line"), out.get("end_line")]}


# ══════════════════════════════════════════════════════════════════════
# Red-2: Variant Maker — 同族变体强化
# ══════════════════════════════════════════════════════════════════════
_VARIANT_PROMPT = """你是红队的毒强化器。下面是一个已通过 admission 的可修毒。

## 原毒信息
- 类型: {mutation_type}
- 变更位置: lines {start}-{end}
- 设计意图: {rationale}

## 原毒 buggy RTL（同位置已被替换为错误代码）
{buggy_rtl}

## 任务
在原毒的基础上强化，使修复更困难，但**必须保持同一种 bug 类型**。
强化方向:
- 如果原毒是有明显边界常量 → 改成更隐蔽的值（如 0→1→2 或 h3f→h7f）
- 如果是分支交换 → 改得更像"优化"（但逻辑仍然错）
- 如果是 off-by-one → 让偏移量更大但仍隐蔽

## 约束
- 必须和原毒是同一 bug family
- 必须可编译
- 只改一处

输出严格 JSON:
{{"start_line": <int>, "end_line": <int>, "new_code": "<>", "mutation_type": "<同族>", "rationale": "<强化了什么>"}}
"""

def red2_variant(poison: dict, work_dir: Path) -> dict:
    """对已 admitted 的毒做同族变体强化."""
    buggy_text = Path(poison["buggy_path"]).read_text(errors="ignore")
    prompt = _VARIANT_PROMPT.format(
        mutation_type=poison.get("mutation_type", ""),
        start=poison.get("range", [1, 1])[0],
        end=poison.get("range", [1, 1])[1],
        rationale=poison.get("rationale", ""),
        buggy_rtl=_numbered(buggy_text),
    )
    out = call_llm(prompt)
    if isinstance(out, list):
        out = next((x for x in out if isinstance(x, dict)), None)
    if not isinstance(out, dict) or "llm_call_error" in out:
        return {"ok": False}

    try:
        # Apply variant TO THE GOLDEN (not the already-buggy) — create a new, stronger bug
        variant_path = apply_block(
            poison["golden_rtl"],
            int(out["start_line"]), int(out["end_line"]),
            out.get("new_code", ""),
            Path(work_dir) / "variant.v",
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "buggy_path": str(variant_path),
            "mutation_type": out.get("mutation_type") or poison.get("mutation_type", ""),
            "rationale": out.get("rationale", "variant"),
            "range": [out.get("start_line"), out.get("end_line")]}


# ══════════════════════════════════════════════════════════════════════
# Red-3: Diversity Critic — 去重 + family 标签
# ══════════════════════════════════════════════════════════════════════
_CRITIC_PROMPT = """你是红队的多样性评审。判断这条毒是否与已有毒池不同。

已有毒 fingerprints: {existing}

当前毒:
- 设计: {design}
- 类型: {mutation_type}
- 原理: {rationale}
- 变更范围: {rng}

输出严格 JSON:
{{"family": "<off_by_one|constant_error|condition_error|operator_error|data_flow_error|state_swap_error|guard_error|other>", "novelty": <0-10, 10=与已有完全不同>, "is_duplicate": <true/false>, "reason": "<1句>"}}
"""

def red3_diversity_critic(design: str, mutation_type: str, rationale: str,
                          rng, existing_fps: set[str]) -> dict:
    """评估 novelty + family 标注 + 去重."""
    prompt = _CRITIC_PROMPT.format(
        existing=", ".join(sorted(existing_fps)[:10]) or "(空池)",
        design=design, mutation_type=mutation_type, rationale=rationale,
        rng=str(rng),
    )
    raw = call_llm(prompt)
    # Fallback to rule-based
    if not isinstance(raw, dict):
        fp = _fingerprint(design, mutation_type, rationale)
        return {"family": "other", "novelty": 5 if fp not in existing_fps else 0,
                "is_duplicate": fp in existing_fps, "reason": "rule-based fallback"}
    raw.setdefault("novelty", 5)
    raw.setdefault("family", "other")
    raw.setdefault("is_duplicate", False)
    return raw


# ══════════════════════════════════════════════════════════════════════
# Red-4: Combiner — 组合两个 atomic probes
# ══════════════════════════════════════════════════════════════════════
_COMBINER_PROMPT = """你是红队的组合器。将两个独立的 atomic bug 组合成 composite probe。

## Bug A
- 类型: {type_a}
- 变更: lines {rng_a}
- 原理: {rationale_a}
- buggy RTL:
{buggy_a}

## Bug B
- 类型: {type_b}
- 变更: lines {rng_b}
- 原理: {rationale_b}
- buggy RTL:
{buggy_b}

## 约束
- 两个 bug 必须在同一个设计中保留
- 组合后总变更 ≤2 edit sites, 不要新增/删除 module
- 组合后必须可编译
- 两个 bug family 不能完全相同

输出严格 JSON:
{{"start_line": <int>, "end_line": <int>, "new_code": "<多行>", "combined_bug_families": ["", ""], "rationale": "<1句>"}}
"""

def red4_combine(design: str, poison_a: dict, poison_b: dict, work_dir: Path) -> dict:
    """组合两个已 admitted 的 atomic probes."""
    text_a = Path(poison_a["buggy_path"]).read_text(errors="ignore")
    text_b = Path(poison_b["buggy_path"]).read_text(errors="ignore")
    prompt = _COMBINER_PROMPT.format(
        type_a=poison_a.get("mutation_type", ""),
        rng_a=str(poison_a.get("range", [])),
        rationale_a=poison_a.get("rationale", ""),
        buggy_a=_numbered(text_a),
        type_b=poison_b.get("mutation_type", ""),
        rng_b=str(poison_b.get("range", [])),
        rationale_b=poison_b.get("rationale", ""),
        buggy_b=_numbered(text_b),
    )
    out = call_llm(prompt)
    if isinstance(out, list):
        out = next((x for x in out if isinstance(x, dict)), None)
    if not isinstance(out, dict) or "llm_call_error" in out:
        return {"ok": False}

    # Apply combined changes to golden
    golden = poison_a.get("golden_rtl", "")
    if not golden or not Path(golden).exists():
        return {"ok": False, "error": "no golden reference"}

    try:
        combined = apply_block(golden, int(out["start_line"]), int(out["end_line"]),
                               out.get("new_code", ""), Path(work_dir) / "composite.v")
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "buggy_path": str(combined),
            "mutation_type": f"composite: {'+'.join(out.get('combined_bug_families', []))}",
            "rationale": out.get("rationale", "combined"),
            "range": [out.get("start_line"), out.get("end_line")],
            "is_composite": True, "atomic_sources": [poison_a.get("fingerprint", ""),
                                                      poison_b.get("fingerprint", "")]}


# ══════════════════════════════════════════════════════════════════════
# Red-5: Boundary Judge — 预测蓝方边界
# ══════════════════════════════════════════════════════════════════════
_JUDGE_PROMPT = """你是红队的边界预测器。对一条 admitted 毒，预测当前蓝方能否修复。

毒信息:
- 设计: {design}
- bug 类型: {mutation_type}
- 原理: {rationale}

蓝方能力:
- 当前蓝方可以修复常量错、条件分支交换等简单 bug
- 对跨周期状态互换、数据流反向等更难的 bug 经常失败
- 蓝方修复预算: 1 attempt

输出严格 JSON:
{{"predicted_boundary": <true/false>, "confidence": <0-10>, "hardness_factors": ["<为什么难>"], "recommend_action": "<admit|strengthen|combine|discard>"}}
"""

def red5_boundary_judge(design: str, mutation_type: str, rationale: str) -> dict:
    """预测此毒是否暴露蓝方边界."""
    prompt = _JUDGE_PROMPT.format(design=design, mutation_type=mutation_type, rationale=rationale)
    out = call_llm(prompt)
    if not isinstance(out, dict):
        return {"predicted_boundary": False, "confidence": 5,
                "hardness_factors": [], "recommend_action": "admit"}
    out.setdefault("predicted_boundary", False)
    out.setdefault("confidence", 5)
    out.setdefault("hardness_factors", [])
    out.setdefault("recommend_action", "admit")
    return out


# ══════════════════════════════════════════════════════════════════════
# Pipeline runner
# ══════════════════════════════════════════════════════════════════════
def run_pipe(designs, cirfix_root, rounds, work_root, out_dir,
             blue_seed=None, blue_evidence_k=1, blue_n_cand=1,
             red_max_tries=3, enable_composite=True):
    work_root = Path(work_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = {d: parse_project(Path(cirfix_root) / d / "project.toml")[0] for d in designs}

    red_mem_path = out_dir / "red_mem.jsonl"
    pack_path = out_dir / "red_pack.jsonl"
    blue_mem = out_dir / "blue_mem.jsonl"
    red_mem_path.write_text(""); pack_path.write_text("")
    blue_mem.write_text(Path(blue_seed).read_text() if blue_seed and Path(blue_seed).exists() else "")

    atomic_pool: list[dict] = []   # all admitted atomic probes
    composite_pool: list[dict] = [] # all admitted composites
    seen_fps: set[str] = set()
    detail = []; curve = []

    for rd in range(1, rounds + 1):
        _l = red_memory.make_red_recall(str(red_mem_path))
        _p = red_memory.make_pack_recall(str(pack_path))
        red_recall = lambda d, _l=_l, _p=_p: (_l(d) + _p(d)).strip()

        round_rec = []
        for d in designs:
            case = cases[d]
            wd = work_root / f"r{rd}" / d

            # Step 1: Red-1 Generator → atomic poison
            print(f"  [R{rd}] {d}: Red-1 generate ...", flush=True)
            gen = red1_generate(case["golden_rtl"], wd / "g1",
                                red_memory_ctx=red_recall(d))
            if not gen.get("ok"):
                print(f"    Red-1 FAILED: {gen.get('error','')[:60]}", flush=True)
                continue

            # Step 1b: Admission gate (golden PASS + compile + oracle fail)
            gate = _admission_gate(case, gen["buggy_path"], wd / "gate")
            if not gate["passed"]:
                print(f"    Admission FAILED: {gate.get('stage','')}", flush=True)
                continue

            # Step 2: Red-2 Variant — strengthen
            print(f"    Red-2 variant ...", flush=True)
            gen["golden_rtl"] = case["golden_rtl"]
            variant = red2_variant(gen, wd / "g2")
            poison = variant if variant.get("ok") else gen
            if variant.get("ok"):
                _ = _admission_gate(case, variant["buggy_path"], wd / "gate_variant")

            # Step 3: Red-3 Diversity Critic
            print(f"    Red-3 critic ...", flush=True)
            critic = red3_diversity_critic(d, poison.get("mutation_type", ""),
                                           poison.get("rationale", ""),
                                           poison.get("range"), seen_fps)

            fp = _fingerprint(d, poison.get("mutation_type", ""), poison.get("rationale", ""))
            if fp in seen_fps:
                print(f"    Red-3: DUPLICATE fp={fp}", flush=True)
                continue
            seen_fps.add(fp)

            poison["fingerprint"] = fp
            poison["family"] = critic.get("family", "other")
            poison["novelty"] = critic.get("novelty", 5)
            poison["design"] = d
            poison["pipeline_round"] = rd
            poison["is_composite"] = False
            atomic_pool.append(poison)
            print(f"    → Atomic admitted: {poison['mutation_type'][:30]} fp={fp[:8]} novelty={critic.get('novelty',5)}", flush=True)

            # Step 4: Red-5 Boundary Judge
            print(f"    Red-5 judge ...", flush=True)
            judge_result = red5_boundary_judge(d, poison.get("mutation_type", ""),
                                                poison.get("rationale", ""))
            poison["boundary_prediction"] = judge_result

            # Step 5: Blue repair
            design_rd = f"{d}__pipe_r{rd}"
            blue_case = {
                "design_name": design_rd, "top_module": case["top_module"],
                "buggy_rtl": poison["buggy_path"], "golden_rtl": case["golden_rtl"],
                "tb_sources": case["tb_sources"], "tb_output": case["tb_output"],
                "deps": case.get("deps", []), "sim_timeout": case.get("sim_timeout", 10.0),
                "mutation_type": poison["mutation_type"],
            }
            res = repair_one(blue_case, wd / "blue",
                             evidence_k=blue_evidence_k, n_candidates=blue_n_cand)
            blue_rep = bool(res.get("repaired"))
            print(f"    Blue: {'修复' if blue_rep else '未修'}", flush=True)

            # Red critic + generalize
            crit = critique(case["golden_rtl"], poison["buggy_path"],
                            poison.get("mutation_type", ""), poison.get("range"),
                            blue_rep, 1)
            red_memory.update(str(red_mem_path), {
                "design": d, "round": rd,
                "mutation_type": poison.get("mutation_type", ""),
                "verdict": crit["verdict"], "challenge": crit.get("challenge"),
                "reason": crit.get("reason", ""), "upgrade_hint": crit.get("upgrade_hint", ""),
                "blue_repaired": blue_rep})
            if crit["verdict"] == "effective":
                pk = generalize(d, poison.get("mutation_type", ""),
                                case["golden_rtl"], poison["buggy_path"],
                                poison.get("range"), crit.get("reason", ""))
                if pk:
                    red_memory.update_pack(str(pack_path), pk)

            if blue_rep:
                with open(blue_mem, "a") as f:
                    f.write(json.dumps({"design": design_rd, "family": poison.get("family"),
                                        "rationale": res.get("llm_rationale", "")},
                                       ensure_ascii=False) + "\n")

            rec = {"round": rd, "design": d, "mutation": poison.get("mutation_type", ""),
                   "family": poison.get("family"), "novelty": poison.get("novelty"),
                   "verdict": crit["verdict"], "challenge": crit.get("challenge"),
                   "blue_repaired": blue_rep,
                   "boundary_predicted": judge_result.get("predicted_boundary"),
                   "fp": fp[:8]}
            detail.append(rec)
            round_rec.append(rec)

        # After all designs processed: Red-4 Combiner (if enabled)
        if enable_composite and len(atomic_pool) >= 2:
            # Pick two most recent, high-novelty, different-family atomics from SAME design
            eligible: dict[str, list[dict]] = {}
            for p in atomic_pool:
                eligible.setdefault(p["design"], []).append(p)
            for design, probes in eligible.items():
                if len(probes) < 2:
                    continue
                probes.sort(key=lambda x: -x.get("novelty", 0))
                a, b = probes[-2], probes[-1]
                if a.get("family") == b.get("family"):
                    continue  # must be different families
                wd = work_root / f"r{rd}" / design
                print(f"    Red-4 combine: {a.get('family')}+{b.get('family')} ...", flush=True)
                combined = red4_combine(design, a, b, wd / "g4")
                if combined.get("ok"):
                    gate = _admission_gate(cases[design], combined["buggy_path"], wd / "gate_composite")
                    if gate["passed"]:
                        combined["design"] = design
                        combined["pipeline_round"] = rd
                        combined["is_composite"] = True
                        combined["family"] = "composite"
                        combined["fingerprint"] = _fingerprint(design, combined["mutation_type"],
                                                                combined["rationale"])
                        composite_pool.append(combined)
                        print(f"    → Composite admitted: {combined['mutation_type'][:40]}", flush=True)

        vd = Counter(r["verdict"] for r in round_rec)
        agg = {"round": rd, "n": len(round_rec),
               "atomic_pool": len(atomic_pool), "composite_pool": len(composite_pool),
               "red_effective": vd.get("effective", 0),
               "blue_rate": round(sum(1 for r in round_rec if r["blue_repaired"]) / max(len(round_rec), 1), 2)}
        curve.append(agg)
        print(f"  R{rd} 汇总: n={agg['n']} atomics={agg['atomic_pool']} composites={agg['composite_pool']} "
              f"red_eff={agg['red_effective']} blue_rate={agg['blue_rate']}\n", flush=True)

    json.dump({"curve": curve, "detail": detail, "atomic_pool_n": len(atomic_pool),
               "composite_pool_n": len(composite_pool)},
              open(out_dir / "pipe_results.json", "w"), ensure_ascii=False, indent=2)
    print(f"结果 → {out_dir}/pipe_results.json")


def main():
    ap = argparse.ArgumentParser(description="R5-Pipe: 5-role Red Pipeline")
    ap.add_argument("--cirfix-root", default="/path/to/cirfix")
    ap.add_argument("--design", action="append", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--blue-evidence-k", type=int, default=1)
    ap.add_argument("--blue-n-cand", type=int, default=1)
    ap.add_argument("--red-max-tries", type=int, default=3)
    ap.add_argument("--no-composite", action="store_true",
                    help="Disable Red-4 Combiner (R5-Pipe-noComp)")
    args = ap.parse_args()
    run_pipe(args.design, args.cirfix_root, args.rounds, args.work, args.out_dir,
             blue_evidence_k=args.blue_evidence_k, blue_n_cand=args.blue_n_cand,
             red_max_tries=args.red_max_tries,
             enable_composite=not args.no_composite)


if __name__ == "__main__":
    main()
