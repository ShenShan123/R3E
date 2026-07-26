"""evidence 选择策略机制小跑: off_by_one 到底要"更多 raw evidence"还是"hybrid evidence"?

三臂(全部固定 n=1, 隔离 generation, 不让 best-of-N selection 混入):
  raw_k1     : 单条 raw per-bit mismatch(baseline)
  raw_k6     : 堆 6 条 raw mismatch(被 2×2 推翻的 evidence-k 轴)
  hybrid     : bus 重组 + 临界对照 + 偏移模式 + 关键 raw recurrence

判读: 若 hybrid >> raw_k1/raw_k6, 则 off_by_one 需要的是结构化诊断和重复 raw 轨迹的组合,
而非单纯堆条数. SEM/95%CI 判据.
"""
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one

S = Path(".iso_semrepair")
N = 3  # reps
# off_by_one / 增量步进敏感 family(counter 步进 + shift 移位)
CASES = [
    "first_counter_overflow__buggy_counter",
    "first_counter_overflow__wadden_buggy1",
    "first_counter_overflow__wadden_buggy2",
    "first_counter_overflow__buggy_overflow",
    "lshift_reg__wadden_buggy1",
    "lshift_reg__kgoliya_buggy1",
]
# (evidence_k, structured_evidence). structured_evidence=True 现已升级为 hybrid prompt.
ARMS = {"raw_k1": (1, False), "raw_k6": (6, False), "hybrid": (1, True)}


def ci95(s1, s2):
    return 1.96 * math.sqrt((s1 / math.sqrt(N)) ** 2 + (s2 / math.sqrt(N)) ** 2)


def main():
    allrows = {r["design_name"]: r for r in
               (json.loads(l) for l in open(S / "survey/valid_functional.jsonl") if l.strip())}
    cases = [allrows[n] for n in CASES]
    print(f"机制小跑: {len(cases)} off_by_one case × 3 臂 × N={N}, n=1(隔离 generation)\n", flush=True)
    out = {}
    per_case = {a: {c["design_name"]: [] for c in cases} for a in ARMS}
    for arm, (k, st) in ARMS.items():
        rates = []
        for rep in range(N):
            ok = 0
            for c in cases:
                r = repair_one(c, S / "evmech" / f"{arm}_r{rep}" / c["design_name"],
                               recall_fn=None, evidence_k=k, n_candidates=1,
                               structured_evidence=st)
                hit = bool(r.get("repaired"))
                ok += hit
                per_case[arm][c["design_name"]].append(hit)
            rates.append(ok / len(cases))
        out[arm] = (statistics.mean(rates),
                    statistics.stdev(rates) if len(rates) > 1 else 0.0, rates)
        print(f"  {arm:11s}: {out[arm][0]:.3f}±{out[arm][1]:.3f}  "
              f"{[round(x,3) for x in rates]}", flush=True)
        json.dump({"summary": {a: list(v) for a, v in out.items()}, "per_case": per_case},
                  open(S / "evidence_mechanism.json", "w"), ensure_ascii=False, indent=2)

    print("\n=== 因子对比 (SEM/95%CI) ===")
    for label, a, b in [("更多条数 raw_k1→raw_k6", "raw_k1", "raw_k6"),
                        ("hybrid raw_k1→hybrid", "raw_k1", "hybrid"),
                        ("hybrid vs 堆条数 raw_k6→hybrid", "raw_k6", "hybrid")]:
        d = out[b][0] - out[a][0]
        c = ci95(out[a][1], out[b][1])
        print(f"  {label}: {out[a][0]:.3f}→{out[b][0]:.3f} Δ{d*100:+.1f}pp "
              f"CI±{c*100:.1f} → {'显著' if abs(d) > c else '噪声内'}")

    print("\n=== per-case 命中(N reps 求和) ===")
    print(f"  {'case':38s} {'raw_k1':>8s} {'raw_k6':>8s} {'hybrid':>8s}")
    for c in cases:
        nm = c["design_name"]
        print(f"  {nm:38s} {sum(per_case['raw_k1'][nm]):>6d}/{N} "
              f"{sum(per_case['raw_k6'][nm]):>6d}/{N} {sum(per_case['hybrid'][nm]):>6d}/{N}")


if __name__ == "__main__":
    main()
