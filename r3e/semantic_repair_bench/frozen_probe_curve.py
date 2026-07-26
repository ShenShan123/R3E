"""P4 frozen-probe learning curve: 蓝方能力随 skill 累积"越修越强"(固定 probe, 红队不参与).

版本递进(每步加一个被 promotion gate 验证的能力):
  B0 baseline(k1n1)  → B1 harness(k6n3, 地基 +26~33pp)
  → B2 conditional(registry routed policy) → B3 routed+strategy(+ family-specific 方法论 hint, G5 +7.6pp)
frozen probe = Red-Fixed-12 全集(固定/红队不改题), 同批 N=3. 有 G5 严格增益打底, 画真实曲线.
区别于已弃的 naive memory learning_curve(噪声曲线): 这里每步增益都经严格对照/promotion gate 验证.
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one
from skill_registry import load_registry, make_strategy_recall, route

S = Path(".iso_semrepair")
N = 3


def main():
    reg = load_registry(str(S / "skills.json"))
    sr = make_strategy_recall()
    probe = [json.loads(l) for l in open(S / "blue_abl/poison_pool.jsonl") if l.strip()]
    print(f"frozen probe = Red-Fixed-12({len(probe)}), 版本递进 N={N}\n", flush=True)

    def policy(version, c):
        if version == "B0_baseline":
            return 1, 1, None
        if version == "B1_harness":
            return 6, 3, None
        pol, _ = route(c, reg)
        if version == "B2_conditional":
            return pol["evidence_k"], pol["n_candidates"], None
        return pol["evidence_k"], pol["n_candidates"], sr  # B3 routed+strategy

    versions = ["B0_baseline", "B1_harness", "B2_conditional", "B3_routed+strategy"]
    out = {}
    for v in versions:
        rates = []
        for rep in range(N):
            ok = 0
            for c in probe:
                k, n, rf = policy(v, c)
                r = repair_one(c, S / "fpc" / f"{v}_r{rep}" / c["design_name"],
                               recall_fn=rf, evidence_k=k, n_candidates=n)
                ok += bool(r.get("repaired"))
            rates.append(ok / len(probe))
        out[v] = (statistics.mean(rates),
                  statistics.stdev(rates) if len(rates) > 1 else 0.0, rates)
        print(f"  {v:20}: {out[v][0]:.3f}±{out[v][1]:.3f}  {[round(r,3) for r in rates]}", flush=True)
        json.dump({k: list(vv) for k, vv in out.items()},
                  open(S / "frozen_probe_curve.json", "w"), ensure_ascii=False, indent=2)

    print("\n=== frozen-probe learning curve（蓝方越修越强，best-so-far） ===")
    bsf = 0.0
    for v in versions:
        m = out[v][0]
        bsf = max(bsf, m)
        print(f"  {v:20}: {m:.3f}  best-so-far={bsf:.3f}")
    print(f"\n  累积增益 B0→B3: {(out['B3_routed+strategy'][0]-out['B0_baseline'][0])*100:+.1f}pp")


if __name__ == "__main__":
    main()
