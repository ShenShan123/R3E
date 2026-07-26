"""消融矩阵补轴①: multi-candidate × evidence-k 2×2 严格 N=3(同 case 同协议, CirFix-39).

旧 §24 单次方向 k1n1 0.538/k6n1 0.564/k1n3 0.667/k6n3 0.718 不可靠(单次跑), 严格重做.
拆开两个因子各自贡献 + 交互: evidence-k(k=1→6) vs multi-candidate(n=1→3). SEM/95%CI 判据.
蓝方系统 functional_repair, 无 memory(纯 harness 因子消融).
"""
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one

S = Path(".iso_semrepair")
N = 3
CONFIGS = {"k1n1": (1, 1), "k6n1": (6, 1), "k1n3": (1, 3), "k6n3": (6, 3)}


def ci95(s1, s2):
    return 1.96 * math.sqrt((s1 / math.sqrt(N)) ** 2 + (s2 / math.sqrt(N)) ** 2)


def main():
    cases = [json.loads(l) for l in open(S / "survey/valid_functional.jsonl") if l.strip()]
    print(f"CirFix-39={len(cases)}, 2×2 (k∈1,6 × n∈1,3) 严格 N={N}\n", flush=True)
    out = {}
    for name, (k, n) in CONFIGS.items():
        rates = []
        for rep in range(N):
            ok = 0
            for c in cases:
                r = repair_one(c, S / "abl2x2" / f"{name}_r{rep}" / c["design_name"],
                               recall_fn=None, evidence_k=k, n_candidates=n)
                ok += bool(r.get("repaired"))
            rates.append(ok / len(cases))
        out[name] = (statistics.mean(rates),
                     statistics.stdev(rates) if len(rates) > 1 else 0.0, rates)
        print(f"  {name}: {out[name][0]:.3f}±{out[name][1]:.3f}  "
              f"{[round(r,3) for r in rates]}", flush=True)
        json.dump({k2: list(v) for k2, v in out.items()},
                  open(S / "ablation_2x2.json", "w"), ensure_ascii=False, indent=2)

    print("\n=== 2×2 因子分解 (SEM/95%CI) ===")
    for label, a, b in [("evidence-k @n=1", "k1n1", "k6n1"),
                        ("evidence-k @n=3", "k1n3", "k6n3"),
                        ("multi-cand @k=1", "k1n1", "k1n3"),
                        ("multi-cand @k=6", "k6n1", "k6n3")]:
        d = out[b][0] - out[a][0]
        c = ci95(out[a][1], out[b][1])
        sig = "显著" if abs(d) > c else "噪声内"
        print(f"  {label}: {a}({out[a][0]:.3f})→{b}({out[b][0]:.3f}) "
              f"Δ{d*100:+.1f}pp CI±{c*100:.1f} → {sig}")
    full = out["k6n3"][0] - out["k1n1"][0]
    print(f"\n  全因子 k1n1→k6n3: Δ{full*100:+.1f}pp (旧单次 0.538→0.718=+18pp)")


if __name__ == "__main__":
    main()
