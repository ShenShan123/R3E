"""G5 skill strategy 层实验: family-specific 修复方法论 hint 是否真提升修复率(超过 G3).

G4 揭示 k/n-only 无 budget 优势 → skill 真正价值需 strategy 层(携带 family-specific 修复方法论).
难 family(constant_error+off_by_one, G3 per-family 0.667~0.750 有提升空间)上对照:
  G3(k6n3, 无 strategy hint) vs G5(k6n3 + strategy 方法论 hint), 严格 N=3.
关键判别: G5 > G3(2σ) → strategy 真提升(方法论可迁移); G5≈G3 → strategy 也无用(像 naive memory).
strategy hint = 修这类 bug 的通用检查清单(非历史 case), 注入 propose 的 {memory} 位置.
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one
from skill_registry import infer_bug_family, make_strategy_recall

S = Path(".iso_semrepair")
N = 3


def run(hard, rep, with_strategy, strat_recall):
    ok = 0
    for c in hard:
        rf = strat_recall if with_strategy else None
        tag = "g5strat" if with_strategy else "g3base"
        r = repair_one(c, S / "g5exp" / f"{tag}_r{rep}" / c["design_name"],
                       recall_fn=rf, evidence_k=6, n_candidates=3)
        ok += bool(r.get("repaired"))
    return ok / len(hard)


def main():
    strat_recall = make_strategy_recall()
    redfix = [json.loads(l) for l in open(S / "blue_abl/poison_pool.jsonl") if l.strip()]
    hard = [c for c in redfix if infer_bug_family(c) in ("constant_error", "off_by_one")]
    print(f"难 family case: {len(hard)} (constant_error + off_by_one)\n", flush=True)

    out = {}
    for label, ws in [("G3_no_hint", False), ("G5_strategy", True)]:
        rates = []
        for rep in range(N):
            rate = run(hard, rep, ws, strat_recall)
            rates.append(rate)
            print(f"  {label} rep{rep}: {rate:.3f}", flush=True)
        out[label] = (statistics.mean(rates),
                      statistics.stdev(rates) if len(rates) > 1 else 0.0, rates)
        json.dump({k: list(v) for k, v in out.items()},
                  open(S / "g5_strategy.json", "w"), ensure_ascii=False, indent=2)

    print("\n" + "=" * 56)
    g3, g5 = out["G3_no_hint"], out["G5_strategy"]
    delta = g5[0] - g3[0]
    noise = 2 * max(g3[1], g5[1], 0.015)
    sig = "✓ strategy 真提升(方法论可迁移)" if delta > noise else (
        "✗ strategy 无用(像 naive memory)" if delta < -noise else "≈噪声内(strategy 无显著效果)")
    print(f"  G3(k6n3 无hint)  : {g3[0]:.3f}±{g3[1]:.3f}  {[round(r,3) for r in g3[2]]}")
    print(f"  G5(k6n3 +strategy): {g5[0]:.3f}±{g5[1]:.3f}  {[round(r,3) for r in g5[2]]}")
    print(f"  → Δ{delta*100:+.1f}pp (2σ={noise:.3f}) → {sig}")


if __name__ == "__main__":
    main()
