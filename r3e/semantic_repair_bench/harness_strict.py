"""地基验证: harness 增益(k6n3 vs k1n1)是否真实(严格 N=3) —— skill promotion 路线的地基.

逻辑: 若 G3(k6n3) 严格 ≫ G0(k1n1)(Δ>2σ) → harness 增益真实, 不是 memory 泄漏 → skill 化有据;
若 G3≈G0 → harness 也落噪声, skill promotion 同样落空(不能重蹈 naive memory 覆辙).
评测集: CirFix-39 + Red-Fixed-12(固定红毒集). 用蓝方系统 functional_repair(已整合 apply_block_patch).
对应方案 6 组消融的 G0/G3(地基), 后续 G4 skill-routed / G5 co-evolution 在此之上.
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one

S = Path(".iso_semrepair")
N = 3


def run(cases, k, n, tag, suite):
    ok = 0
    for c in cases:
        r = repair_one(c, S / "harness" / f"{suite}_{tag}" / c["design_name"],
                       recall_fn=None, evidence_k=k, n_candidates=n)
        ok += bool(r.get("repaired"))
    return ok / len(cases)


def avg(cases, k, n, suite):
    rates = []
    for rep in range(N):
        rates.append(run(cases, k, n, f"k{k}n{n}_r{rep}", suite))
        print(f"  [{suite}] k{k}n{n} rep{len(rates)-1}: {rates[-1]:.3f}", flush=True)
    return statistics.mean(rates), (statistics.stdev(rates) if len(rates) > 1 else 0.0), rates


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    cirfix = [json.loads(l) for l in open(S / "survey/valid_functional.jsonl") if l.strip()]
    redfix = [json.loads(l) for l in open(S / "blue_abl/poison_pool.jsonl") if l.strip()]
    print(f"CirFix-39={len(cirfix)}, Red-Fixed-12={len(redfix)} (only={only})\n", flush=True)

    suites = [("CirFix-39", cirfix), ("Red-Fixed-12", redfix)]
    if only:
        suites = [(s, c) for s, c in suites if only in s]
    out = {}
    for suite, cases in suites:
        # G0/G3 均为 R³E 蓝方 harness 配置消融臂(非外部系统); suite 为数据集名
        print(f"=== 数据集 {suite}: R³E[G0:k1n1] vs R³E[G3:k6n3] 严格 N={N} ===", flush=True)
        g0 = avg(cases, 1, 1, suite)
        g3 = avg(cases, 6, 3, suite)
        delta = g3[0] - g0[0]
        noise = 2 * max(g0[1], g3[1], 0.015)
        sig = "显著(R³E harness 增益真实)" if delta > noise else "≈噪声内(harness 增益不立)"
        out[suite] = {"G0": g0, "G3": g3, "delta": delta, "sig": sig}
        print(f"  → R³E[G0] {g0[0]:.3f}±{g0[1]:.3f} | R³E[G3] {g3[0]:.3f}±{g3[1]:.3f} | "
              f"Δ{delta*100:+.1f}pp (2σ={noise:.3f}) → {sig}\n", flush=True)
        json.dump({k: {"G0": list(v["G0"]), "G3": list(v["G3"]),
                       "delta": v["delta"], "sig": v["sig"]} for k, v in out.items()},
                  open(S / f"harness_strict{'_'+only if only else ''}.json", "w"),
                  ensure_ascii=False, indent=2)

    print("=" * 60)
    print("=== 地基结论 ===")
    for suite, v in out.items():
        print(f"  {suite}: Δ{v['delta']*100:+.1f}pp → {v['sig']}")


if __name__ == "__main__":
    main()
