"""G4 skill-routed 实验: conditional registry 是否省 budget 同时保持修复率.

对比(Red-Fixed-12, 有 bug_family 标签): G0(全k1n1) / G3(全k6n3) / G4(skill registry 路由 per-family).
G4 价值 = 接近 G3 修复率, 但 budget 显著少(易 family 用 k1n1, 难 family 才 k6n3).
budget = sum(n_candidates_tried). 蓝方系统 functional_repair + skill_registry.
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one
from skill_registry import load_registry, route

S = Path(".iso_semrepair")
N = 3


def run_config(redfix, rep, mode, reg):
    ok, budget = 0, 0
    for c in redfix:
        if mode == "G0":
            k, n = 1, 1
        elif mode == "G3":
            k, n = 6, 3
        else:  # G4 skill-routed
            pol, _ = route(c, reg)
            k, n = pol["evidence_k"], pol["n_candidates"]
        r = repair_one(c, S / "g4exp" / f"{mode}_r{rep}" / c["design_name"],
                       recall_fn=None, evidence_k=k, n_candidates=n)
        ok += bool(r.get("repaired"))
        budget += r.get("n_candidates_tried", n)
    return ok / len(redfix), budget


def main():
    reg = load_registry(str(S / "skills.json"))
    redfix = [json.loads(l) for l in open(S / "blue_abl/poison_pool.jsonl") if l.strip()]
    print(f"Red-Fixed-12={len(redfix)}, registry={len(reg)} skills\n", flush=True)

    out = {}
    for mode in ["G0", "G3", "G4"]:
        rates, budgets = [], []
        for rep in range(N):
            rate, bud = run_config(redfix, rep, mode, reg)
            rates.append(rate)
            budgets.append(bud)
            print(f"  {mode} rep{rep}: rate={rate:.3f} budget={bud}", flush=True)
        out[mode] = {"rate": statistics.mean(rates),
                     "std": statistics.stdev(rates) if len(rates) > 1 else 0.0,
                     "budget": statistics.mean(budgets)}
        json.dump(out, open(S / "g4_skill_routed.json", "w"), ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("=== G4 skill-routed vs G0/G3 (Red-Fixed-12, N=3) ===")
    for m in ["G0", "G3", "G4"]:
        v = out[m]
        print(f"  {m}: rate {v['rate']:.3f}±{v['std']:.3f}  budget≈{v['budget']:.0f}")
    g3, g4 = out["G3"], out["G4"]
    budget_save = (g3["budget"] - g4["budget"]) / g3["budget"] * 100
    rate_drop = (g3["rate"] - g4["rate"]) * 100
    print(f"\n  G4 vs G3: 修复率 Δ{-rate_drop:+.1f}pp, budget 省 {budget_save:.0f}%")
    print(f"  → {'✓ conditional 有效(省 budget 保修复率)' if rate_drop < 8 and budget_save > 10 else '需再调'}")


if __name__ == "__main__":
    main()
