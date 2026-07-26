"""head-to-head 我方: 蓝方增强配置(memory + evidence_k=6 + best-of-3) 在 CirFix 39 有效功能
case 真跑. 与 RTL-Repair 同 39 case、同 oracle_gate(repair_one 内部判 patched) → 完全同 gate.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one
from memory_store import make_recall_fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--memory", default=None, help="蓝记忆; 缺省=no-memory(zero-shot 保守对照)")
    ap.add_argument("--evidence-k", type=int, default=6)
    ap.add_argument("--n-cand", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.manifest) if l.strip()]
    recall = make_recall_fn(args.memory) if args.memory else None
    results = []
    for r in rows:
        rr = repair_one(r, Path(args.work) / r["design_name"], recall_fn=recall,
                        evidence_k=args.evidence_k, n_candidates=args.n_cand)
        rec = {"design": r["design_name"], "repaired": bool(rr.get("repaired")),
               "n_tried": rr.get("n_candidates_tried"), "used_memory": rr.get("used_memory")}
        results.append(rec)
        with open(args.out, "w") as f:
            for x in results:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        print(f"  {r['design_name']:40} {'修复' if rec['repaired'] else '未修':4} "
              f"(tried {rec['n_tried']})", flush=True)

    n = len(results)
    rep = sum(x["repaired"] for x in results)
    print(f"\n=== 我方(增强蓝 k={args.evidence_k} n={args.n_cand} +memory) @ {n} CirFix case ===")
    print(f"修复率: {rep}/{n} = {rep/n:.3f}")


if __name__ == "__main__":
    main()
