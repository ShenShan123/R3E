"""红蓝双向 co-evolution（军备竞赛）: 红升级毒(critic+经验包) + 蓝 distill 自进化(应对红毒).

对比基线 red_evolve_cx(蓝记忆固定 memory_L1, 修复率被红毒压到 0.25):
本实验蓝记忆**动态累积**(蓝修成功→distill 入蓝记忆), 看蓝修复率能否对抗红毒升级不掉/回升
=蓝队经红队投毒训练自进化"越修越强"的实证.

详细记录每轮每设计数据(论文核心): 红毒类型/verdict/挑战度 + 蓝修复/用memory + 各记忆规模演化.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import red_memory
from cirfix_adapter import parse_project
from functional_repair import repair_one
from memory_store import make_recall_fn
from red_critic import critique, generalize
from red_mutator import generate_repairable_poison


def _count(p):
    return sum(1 for l in open(p) if l.strip()) if Path(p).exists() else 0


def _blue_distill(blue_mem_path, design_rd, top, res):
    """蓝修成功 → skill 入蓝记忆(append, memory_store 格式)."""
    skill = {"design": design_rd, "family": design_rd.split("__")[0], "top": top,
             "patch_range": res.get("patch_range"), "rationale": res.get("llm_rationale", "")}
    with open(blue_mem_path, "a") as f:
        f.write(json.dumps(skill, ensure_ascii=False) + "\n")


def run(designs, cirfix_root, rounds, work_root, out_dir, blue_seed=None,
        blue_evidence_k=1, blue_n_cand=1, blue_preflight_registry=None,
        blue_structured_evidence=False, blue_template_preflight=False,
        red_max_tries=3):
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
    blue_seed_n = _count(blue_mem)

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
            wd = work_root / f"r{rd}" / d
            p = generate_repairable_poison(
                cases[d],
                wd / "red",
                max_tries=red_max_tries,
                red_recall_fn=red_recall,
                design=d,
            )
            if not p.get("ok"):
                red_memory.update(str(red_mem), {
                    "design": d,
                    "round": rd,
                    "mutation_type": "generation_failed",
                    "verdict": "generation_failed",
                    "challenge": 0,
                    "reason": p.get("reason", "red mutator failed to produce valid poison"),
                    "upgrade_hint": (
                        "优先生成能编译且只改变一处语义行为的保守变异；避免新增/删除 module、"
                        "避免大块替换，先选择边界常量、条件、索引、单个赋值方向等可 gate 的局部修改。"
                    ),
                    "blue_repaired": None,
                })
                print(f"  [R{rd}] {d:20} 红队失败: {p['reason'][:40]}", flush=True)
                continue
            design_rd = f"{d}__r{rd}"
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

            # 红侧自进化: 失败教训入红记忆 / 有效毒泛化入经验包
            red_memory.update(str(red_mem), {
                "design": d, "round": rd, "mutation_type": p["mutation_type"],
                "verdict": crit["verdict"], "challenge": crit.get("challenge"),
                "reason": crit.get("reason", ""), "upgrade_hint": crit.get("upgrade_hint", ""),
                "blue_repaired": blue_rep})
            if crit["verdict"] == "effective":
                pk = generalize(d, p["mutation_type"], p["golden_rtl"], p["buggy_path"],
                                p.get("range"), crit.get("reason", ""))
                if pk:
                    red_memory.update_pack(str(pack), pk)
            # 蓝侧自进化: 修成功 → distill 入蓝记忆(下轮更强)
            if blue_rep:
                _blue_distill(str(blue_mem), design_rd, p["top_module"], res)

            rec = {
                "round": rd, "design": d, "red_mutation": p["mutation_type"],
                "red_verdict": crit["verdict"], "red_challenge": crit.get("challenge"),
                "red_stealth": crit.get("stealth"), "red_evidence": p.get("evidence", ""),
                "blue_repaired": blue_rep, "blue_used_memory": res.get("used_memory", False),
                "blue_used_strategy": res.get("used_strategy", False),
                "blue_preflight": res.get("preflight", {}),
                "blue_effective_evidence_k": res.get("effective_evidence_k"),
                "blue_effective_n_candidates": res.get("effective_n_candidates"),
                "red_rationale": (p.get("rationale", "") or "")[:90],
                "blue_rationale": (res.get("llm_rationale", "") or "")[:90],
            }
            detail.append(rec)
            round_rec.append(rec)
            print(f"  [R{rd}] {d:20} 红毒({p['mutation_type'][:12]}) "
                  f"verdict={crit['verdict']:11} chal={crit.get('challenge')} "
                  f"蓝={'修复' if blue_rep else '未修'}[mem={res.get('used_memory')}]", flush=True)

        vd = Counter(r["red_verdict"] for r in round_rec)
        chals = [r["red_challenge"] for r in round_rec if r.get("red_challenge")]
        agg = {
            "round": rd, "n": len(round_rec),
            "red_effective": vd.get("effective", 0),
            "red_weak": vd.get("too_weak", 0) + vd.get("too_obvious", 0),
            "avg_challenge": round(sum(chals) / len(chals), 2) if chals else 0,
            "blue_repair_rate": round(sum(r["blue_repaired"] for r in round_rec) / len(round_rec), 2)
                                if round_rec else 0,
            "red_mem": _count(red_mem), "pack": _count(pack),
            "blue_mem": _count(blue_mem), "blue_mem_gained": _count(blue_mem) - blue_seed_n,
        }
        curve.append(agg)
        print(f"  R{rd} 汇总: red_eff={agg['red_effective']} avg_chal={agg['avg_challenge']} "
              f"蓝修复率={agg['blue_repair_rate']} | 经验包={agg['pack']} 蓝记忆={agg['blue_mem']}"
              f"(+{agg['blue_mem_gained']})\n", flush=True)

    print("=== 红蓝 co-evolution 曲线 ===")
    print("round  红eff  avg挑战  蓝修复率  经验包  蓝记忆(+增)")
    for c in curve:
        print(f"  R{c['round']}    {c['red_effective']:2}    {c['avg_challenge']:.2f}    "
              f"{c['blue_repair_rate']:.2f}     {c['pack']:2}     "
              f"{c['blue_mem']}(+{c['blue_mem_gained']})")
    json.dump({"curve": curve, "detail": detail, "blue_seed_n": blue_seed_n},
              open(out_dir / "coevolve.json", "w"), ensure_ascii=False, indent=2)
    json.dump({
        "designs": designs,
        "rounds": rounds,
        "blue_evidence_k_requested": blue_evidence_k,
        "blue_n_candidates_requested": blue_n_cand,
        "blue_preflight_registry": str(blue_preflight_registry) if blue_preflight_registry else None,
        "blue_structured_evidence": blue_structured_evidence,
        "blue_template_preflight": blue_template_preflight,
        "blue_memory_recall_enabled": not bool(blue_preflight_registry),
        "red_max_tries": red_max_tries,
    }, open(out_dir / "params.json", "w"), ensure_ascii=False, indent=2)
    print(f"\n详细数据 → {out_dir}/coevolve.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cirfix-root", default="/path/to/cirfix")
    ap.add_argument("--design", action="append", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--blue-seed", default=None, help="蓝记忆种子(如 memory_L1); 缺省空起步")
    ap.add_argument("--blue-evidence-k", type=int, default=1, help="蓝方证据数(1=baseline,6=增强)")
    ap.add_argument("--blue-n-cand", type=int, default=1, help="蓝方多候选(1=baseline,3=增强)")
    ap.add_argument("--blue-preflight-registry", default=None,
                    help="启用 current skill registry/preflight；启用后不再走 raw blue memory prompt recall")
    ap.add_argument("--blue-structured-evidence", action="store_true",
                    help="蓝方使用 hybrid structured evidence + raw recurrence")
    ap.add_argument("--blue-template-preflight", action="store_true",
                    help="启用 deterministic/template preflight hook")
    ap.add_argument("--red-max-tries", type=int, default=3,
                    help="红队每条 poison 的最大生成尝试次数；失败也会入 red memory")
    args = ap.parse_args()
    run(args.design, args.cirfix_root, args.rounds, args.work, args.out_dir, args.blue_seed,
        blue_evidence_k=args.blue_evidence_k, blue_n_cand=args.blue_n_cand,
        blue_preflight_registry=args.blue_preflight_registry,
        blue_structured_evidence=args.blue_structured_evidence,
        blue_template_preflight=args.blue_template_preflight,
        red_max_tries=args.red_max_tries)


if __name__ == "__main__":
    main()
