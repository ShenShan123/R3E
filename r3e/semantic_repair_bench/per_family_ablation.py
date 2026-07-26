"""per-family 消融: 哪些 bug_family 真需 k6n3(harness 增益大), 哪些 k1n1 就够(省 budget).

目的: 让 skill registry 从"全 k6n3"变成真正 conditional——不同 bug_family 路由不同 action_policy.
Red-Fixed-12 按 mutation_type→family 拆, per-case k1n1 vs k6n3 严格 N=3. 蓝方系统 functional_repair.
(样本少: 每 family 1~4 case, 先看方向; 后续红队按 family 扩样再固化 registry policy)
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one
from skill_registry import infer_bug_family

S = Path(".iso_semrepair")
N = 3


def main():
    redfix = [json.loads(l) for l in open(S / "blue_abl/poison_pool.jsonl") if l.strip()]
    res = {}
    for c in redfix:
        fam = infer_bug_family(c)
        rec = {"fam": fam, "k1n1": [], "k6n3": []}
        for k, n, cfg in [(1, 1, "k1n1"), (6, 3, "k6n3")]:
            for rep in range(N):
                r = repair_one(c, S / "perfam" / f"{cfg}_r{rep}" / c["design_name"],
                               recall_fn=None, evidence_k=k, n_candidates=n)
                rec[cfg].append(bool(r.get("repaired")))
        res[c["design_name"]] = rec
        print(f"  {c['design_name']:38} {fam:14} k1n1={sum(rec['k1n1'])}/{N*1} "
              f"k6n3={sum(rec['k6n3'])}/{N*1}", flush=True)
        json.dump(res, open(S / "per_family_ablation.json", "w"), ensure_ascii=False)

    fam_agg = defaultdict(lambda: {"k1n1": [], "k6n3": []})
    for d, v in res.items():
        fam_agg[v["fam"]]["k1n1"].extend(v["k1n1"])
        fam_agg[v["fam"]]["k6n3"].extend(v["k6n3"])

    print("\n" + "=" * 64)
    print("=== per-family harness 增益 (Red-Fixed-12, 决定 registry conditional policy) ===")
    policy = {}
    for fam, a in sorted(fam_agg.items()):
        g0 = statistics.mean(a["k1n1"])
        g3 = statistics.mean(a["k6n3"])
        ncase = len(a["k1n1"]) // N
        delta = g3 - g0
        # 决策: 增益>15pp → 需 k6n3; 否则 k1n1 够(省 budget)
        rec_policy = "k6n3(需增强)" if delta > 0.15 else "k1n1(够用,省budget)"
        policy[fam] = {"g0": round(g0, 3), "g3": round(g3, 3), "delta": round(delta, 3),
                       "ncase": ncase, "recommend": rec_policy}
        print(f"  {fam:16}: G0 {g0:.3f} → G3 {g3:.3f}  Δ{delta*100:+.1f}pp  "
              f"(n={ncase}×{N}) → {rec_policy}")
    json.dump(policy, open(S / "per_family_policy.json", "w"), ensure_ascii=False, indent=2)
    print(f"\n  policy 存 {S/'per_family_policy.json'}")


if __name__ == "__main__":
    main()
