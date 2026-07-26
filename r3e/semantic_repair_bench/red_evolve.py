"""红队自进化(多轮红蓝对抗): 红不断深化毒样 + 增加新毒, 喂蓝方更多样实践.

每轮每 design:
  红生毒(recall 红记忆→避弱毒/按 upgrade 升级) → **升级覆盖旧毒例**(poison_set[design] 替换)
  → 蓝 with-memory 修 → critic(资深工程师据蓝结果评估) → 教训入红记忆(下轮升级依据).
跟踪: 红毒 verdict 演化(too_weak↓/effective↑)、avg 挑战度↑、蓝修复率(毒变强→应下降).
毒例集 poison_set = 每 design 当前最强毒(升级覆盖, 非堆积弱毒) = 有效 curriculum.
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
from skill_registry import make_strategy_recall  # 晋级版: skill strategy 方法论(非 naive memory)
from red_critic import critique, generalize
from red_mutator import generate_repairable_poison


def run(designs, cirfix_root, rounds, work_root, blue_memory, red_mem_path, out, pack_path=None):
    work_root = Path(work_root)
    pack_path = pack_path or str(Path(red_mem_path).parent / "red_pack.jsonl")
    cases = {d: parse_project(Path(cirfix_root) / d / "project.toml")[0] for d in designs}
    blue_recall = make_strategy_recall()  # 蓝方修复注入 skill strategy 方法论(晋级版统一)
    Path(red_mem_path).parent.mkdir(parents=True, exist_ok=True)
    Path(red_mem_path).write_text("")  # 红记忆空起步
    Path(pack_path).write_text("")     # 经验包空起步
    poison_set = {}   # design -> 当前毒(升级覆盖旧毒)
    curve = []

    for rd in range(1, rounds + 1):
        # 合并 recall: 失败教训(避弱毒/升级) + 高级毒策略经验包(泛化自有效毒)
        _lesson = red_memory.make_red_recall(red_mem_path)
        _pack = red_memory.make_pack_recall(pack_path)
        red_recall = lambda d, _l=_lesson, _p=_pack: (_l(d) + _p(d)).strip()
        round_rec = []
        for d in designs:
            wd = work_root / f"r{rd}" / d
            p = generate_repairable_poison(cases[d], wd / "red",
                                           red_recall_fn=red_recall, design=d)
            if not p.get("ok"):
                round_rec.append({"design": d, "red_ok": False, "reason": p["reason"]})
                print(f"  [R{rd}] {d:18} 红队失败: {p['reason'][:40]}", flush=True)
                continue
            poison_set[d] = p  # ★ 升级覆盖旧毒例

            blue_case = {
                "design_name": f"{d}__redpoison", "top_module": p["top_module"],
                "buggy_rtl": p["buggy_path"], "golden_rtl": p["golden_rtl"],
                "tb_sources": p["tb_sources"], "tb_output": p["tb_output"],
                "deps": p["deps"], "sim_timeout": p["sim_timeout"],
            }
            res = repair_one(blue_case, wd / "blue", recall_fn=blue_recall)
            blue_rep = bool(res.get("repaired"))

            crit = critique(p["golden_rtl"], p["buggy_path"], p["mutation_type"],
                            p.get("range"), blue_rep, p.get("tries"))
            red_memory.update(red_mem_path, {
                "design": d, "round": rd, "mutation_type": p["mutation_type"],
                "verdict": crit["verdict"], "stealth": crit.get("stealth"),
                "challenge": crit.get("challenge"), "reason": crit.get("reason", ""),
                "upgrade_hint": crit.get("upgrade_hint", ""), "blue_repaired": blue_rep,
            })
            # ★ 有效毒(隐蔽有挑战) → 泛化成可迁移毒策略 → 经验包入库, 供后续升级毒样
            if crit["verdict"] == "effective":
                pk = generalize(d, p["mutation_type"], p["golden_rtl"], p["buggy_path"],
                                p.get("range"), crit.get("reason", ""))
                if pk and red_memory.update_pack(pack_path, pk):
                    print(f"        ↳ 经验包+1: {pk['strategy'][:60]}", flush=True)
            round_rec.append({"design": d, "red_ok": True, "mutation_type": p["mutation_type"],
                              "verdict": crit["verdict"], "challenge": crit.get("challenge"),
                              "blue_repaired": blue_rep})
            print(f"  [R{rd}] {d:18} 毒({p['mutation_type'][:14]}) "
                  f"verdict={crit['verdict']:11} chal={crit.get('challenge')} "
                  f"blue={'修复' if blue_rep else '未修'}", flush=True)

        valid = [r for r in round_rec if r.get("red_ok")]
        vd = Counter(r["verdict"] for r in valid)
        chals = [r["challenge"] for r in valid if r.get("challenge")]
        avg_chal = sum(chals) / len(chals) if chals else 0
        blue_rate = sum(r["blue_repaired"] for r in valid) / len(valid) if valid else 0
        curve.append({
            "round": rd, "verdict": dict(vd), "avg_challenge": round(avg_chal, 2),
            "blue_repair_rate": round(blue_rate, 2),
            "too_weak": vd.get("too_weak", 0) + vd.get("too_obvious", 0),
            "effective": vd.get("effective", 0), "n": len(valid),
        })
        print(f"  R{rd} 汇总: verdict={dict(vd)} avg_chal={avg_chal:.2f} "
              f"blue_rate={blue_rate:.2f}\n", flush=True)

    print("=== 红队自进化曲线(毒不断深化) ===")
    print("round  effective  too_weak  avg挑战度  蓝修复率")
    for c in curve:
        print(f"  R{c['round']}     {c['effective']:2}         {c['too_weak']:2}        "
              f"{c['avg_challenge']:.2f}      {c['blue_repair_rate']:.2f}")
    pack_n = sum(1 for l in open(pack_path) if l.strip()) if Path(pack_path).exists() else 0
    print(f"\n经验包(泛化毒策略) 累积 = {pack_n} 条 → {pack_path}")
    json.dump({"curve": curve, "red_mem_stats": red_memory.stats(red_mem_path),
               "pack_count": pack_n}, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"curve → {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cirfix-root", default="/path/to/cirfix")
    ap.add_argument("--design", action="append", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--work", required=True)
    ap.add_argument("--blue-memory", default=None)
    ap.add_argument("--red-memory", required=True)
    ap.add_argument("--pack", default=None, help="泛化毒策略经验包路径; 缺省 red-memory 同目录")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run(args.design, args.cirfix_root, args.rounds, args.work,
        args.blue_memory, args.red_memory, args.out, pack_path=args.pack)


if __name__ == "__main__":
    main()
