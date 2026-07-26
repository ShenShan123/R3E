#!/usr/bin/env python3
"""R5-Ind wrapper: 5 独立红方 co-evolution, 完整 correctness-guided 进化机制.

使用 co_evolve.py 的核心循环（red critic/red memory/generalize/blue_distill），
但每轮每 design 支持 P 个独立红方 agent 各生成 1 毒，蓝方分别修复。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cirfix_adapter import parse_project
from functional_repair import repair_one
from memory_store import make_recall_fn
from red_critic import critique, generalize
from red_mutator import generate_repairable_poison
import red_memory


def run(designs, cirfix_root, rounds, red_agents, work_root, out_dir,
        blue_seed=None, blue_evidence_k=1, blue_n_cand=1,
        blue_preflight_registry=None, blue_structured_evidence=False,
        blue_template_preflight=False, red_max_tries=3):
    work_root = Path(work_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = {d: parse_project(Path(cirfix_root) / d / "project.toml")[0] for d in designs}
    red_mem = out_dir / "red_mem.jsonl"
    pack = out_dir / "red_pack.jsonl"
    blue_mem = out_dir / "blue_mem.jsonl"
    red_mem.write_text("")
    pack.write_text("")
    blue_mem.write_text(Path(blue_seed).read_text() if blue_seed and Path(blue_seed).exists() else "")
    blue_seed_n = sum(1 for _ in open(blue_mem) if _.strip()) if blue_mem.exists() else 0

    detail = []
    curve = []
    for rd in range(1, rounds + 1):
        _l = red_memory.make_red_recall(str(red_mem))
        _p = red_memory.make_pack_recall(str(pack))
        red_recall = lambda d, _l=_l, _p=_p: (_l(d) + _p(d)).strip()
        blue_recall = (
            make_recall_fn(str(blue_mem))
            if (blue_mem.stat().st_size > 0 and not blue_preflight_registry)
            else None
        )

        round_rec = []
        for d in designs:
            for agent_i in range(red_agents):
                wd = work_root / f"r{rd}" / d / f"agent_{agent_i}"

                # Temperature diversity per agent
                old_temp = os.environ.get("LLM_TEMPERATURE")
                temp = 0.70 + (agent_i % 10) * 0.03
                os.environ["LLM_TEMPERATURE"] = str(temp)
                try:
                    p = generate_repairable_poison(
                        cases[d], wd / "red",
                        max_tries=red_max_tries,
                        red_recall_fn=red_recall,
                        design=d,
                    )
                finally:
                    if old_temp is not None:
                        os.environ["LLM_TEMPERATURE"] = old_temp
                    else:
                        os.environ.pop("LLM_TEMPERATURE", None)

                agent_tag = f"{d}/agent_{agent_i}"
                if not p.get("ok"):
                    red_memory.update(str(red_mem), {
                        "design": d, "round": rd, "agent": agent_i,
                        "mutation_type": "generation_failed",
                        "verdict": "generation_failed", "challenge": 0,
                        "reason": p.get("reason", "generation failed"),
                        "upgrade_hint": (
                            "优先生成能编译且只改变一处语义行为的保守变异；"
                            "避免新增/删除 module、避免大块替换。"
                        ),
                        "blue_repaired": None,
                    })
                    print(f"  [R{rd}] {agent_tag:30s} 红队失败: {p['reason'][:40]}", flush=True)
                    continue

                design_rd = f"{d}__r{rd}_a{agent_i}"
                blue_case = {
                    "design_name": design_rd, "top_module": p["top_module"],
                    "buggy_rtl": p["buggy_path"], "golden_rtl": p["golden_rtl"],
                    "tb_sources": p["tb_sources"], "tb_output": p["tb_output"],
                    "deps": p["deps"], "sim_timeout": p["sim_timeout"],
                    "mutation_type": p["mutation_type"],
                }
                res = repair_one(blue_case, wd / "blue", recall_fn=blue_recall,
                                 evidence_k=blue_evidence_k, n_candidates=blue_n_cand,
                                 structured_evidence=blue_structured_evidence,
                                 preflight_registry=blue_preflight_registry,
                                 enable_template_preflight=blue_template_preflight)
                blue_rep = bool(res.get("repaired"))
                crit = critique(p["golden_rtl"], p["buggy_path"], p["mutation_type"],
                                p.get("range"), blue_rep, p.get("tries"))

                red_memory.update(str(red_mem), {
                    "design": d, "round": rd, "agent": agent_i,
                    "mutation_type": p["mutation_type"],
                    "verdict": crit["verdict"], "challenge": crit.get("challenge"),
                    "reason": crit.get("reason", ""), "upgrade_hint": crit.get("upgrade_hint", ""),
                    "blue_repaired": blue_rep})
                if crit["verdict"] == "effective":
                    pk = generalize(d, p["mutation_type"], p["golden_rtl"], p["buggy_path"],
                                    p.get("range"), crit.get("reason", ""))
                    if pk:
                        red_memory.update_pack(str(pack), pk)

                if blue_rep:
                    skill = {"design": design_rd, "family": d, "top": p["top_module"],
                             "patch_range": res.get("patch_range"),
                             "rationale": res.get("llm_rationale", "")}
                    with open(blue_mem, "a") as f:
                        f.write(json.dumps(skill, ensure_ascii=False) + "\n")

                rec = {
                    "round": rd, "design": d, "agent": agent_i,
                    "red_mutation": p["mutation_type"],
                    "red_verdict": crit["verdict"], "red_challenge": crit.get("challenge"),
                    "red_stealth": crit.get("stealth"),
                    "blue_repaired": blue_rep,
                    "blue_used_memory": res.get("used_memory", False),
                    "blue_rationale": (res.get("llm_rationale", "") or "")[:90],
                }
                detail.append(rec)
                round_rec.append(rec)
                print(f"  [R{rd}] {agent_tag:30s} 毒({p['mutation_type'][:14]}) "
                      f"verdict={crit['verdict']:11} chal={crit.get('challenge')} "
                      f"蓝={'修复' if blue_rep else '未修'}", flush=True)

        from collections import Counter
        vd = Counter(r["red_verdict"] for r in round_rec)
        chals = [r["red_challenge"] for r in round_rec if r.get("red_challenge")]
        blue_mem_count = sum(1 for _ in open(blue_mem) if _.strip()) if blue_mem.exists() else 0
        agg = {
            "round": rd, "n": len(round_rec),
            "red_effective": vd.get("effective", 0),
            "red_weak": vd.get("too_weak", 0) + vd.get("too_obvious", 0),
            "avg_challenge": round(sum(chals) / len(chals), 2) if chals else 0,
            "blue_repair_rate": round(sum(r["blue_repaired"] for r in round_rec) / len(round_rec), 2)
                                if round_rec else 0,
            "red_mem": sum(1 for _ in open(red_mem) if _.strip()),
            "pack": sum(1 for _ in open(pack) if _.strip()),
            "blue_mem": blue_mem_count,
            "blue_mem_gained": blue_mem_count - blue_seed_n,
        }
        curve.append(agg)
        print(f"  R{rd} 汇总: n={agg['n']} red_eff={agg['red_effective']} "
              f"blue_rate={agg['blue_repair_rate']} "
              f"blue_mem={agg['blue_mem']}(+{agg['blue_mem_gained']})\n", flush=True)

    json.dump({"curve": curve, "detail": detail, "blue_seed_n": blue_seed_n},
              open(out_dir / "coevolve.json", "w"), ensure_ascii=False, indent=2)
    json.dump({
        "designs": designs, "rounds": rounds, "red_agents": red_agents,
        "blue_evidence_k": blue_evidence_k, "blue_n_candidates": blue_n_cand,
        "blue_preflight_registry": str(blue_preflight_registry) if blue_preflight_registry else None,
        "blue_structured_evidence": blue_structured_evidence,
        "blue_template_preflight": blue_template_preflight,
        "blue_memory_recall_enabled": not bool(blue_preflight_registry),
        "red_max_tries": red_max_tries,
    }, open(out_dir / "params.json", "w"), ensure_ascii=False, indent=2)
    print(f"\n结果 → {out_dir}/coevolve.json")


def main():
    ap = argparse.ArgumentParser(description="R5-Ind: Multi-red co-evolution")
    ap.add_argument("--cirfix-root", default="/path/to/cirfix")
    ap.add_argument("--design", action="append", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--red-agents", type=int, default=5, help="红方 agent 数量")
    ap.add_argument("--work", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--blue-seed", default=None)
    ap.add_argument("--blue-evidence-k", type=int, default=1)
    ap.add_argument("--blue-n-cand", type=int, default=1)
    ap.add_argument("--blue-preflight-registry", default=None)
    ap.add_argument("--blue-structured-evidence", action="store_true")
    ap.add_argument("--blue-template-preflight", action="store_true")
    ap.add_argument("--red-max-tries", type=int, default=3)
    args = ap.parse_args()
    run(args.design, args.cirfix_root, args.rounds, args.red_agents,
        args.work, args.out_dir, args.blue_seed,
        blue_evidence_k=args.blue_evidence_k, blue_n_cand=args.blue_n_cand,
        blue_preflight_registry=args.blue_preflight_registry,
        blue_structured_evidence=args.blue_structured_evidence,
        blue_template_preflight=args.blue_template_preflight,
        red_max_tries=args.red_max_tries)


if __name__ == "__main__":
    main()
