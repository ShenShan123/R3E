"""L1/L2 memory 消融 batch runner.

每 case 跑 N 次重复(LLM 非确定性 → 统计意义), 统计:
  pass@1 = 平均单次成功率(N 次中成功比例的均值)
  pass@N = 至少一次成功的 case 比例
memory 唯一变量: recall_fn=None → L1(no-memory); recall_fn=<recall> → L2(with-memory).
L1/L2 用同 manifest/同 N, 只切 recall_fn, 保证可比.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one


def run_batch(manifest, work_root, n_repeat, recall_fn=None, out_jsonl=None, label="L1"):
    rows = [json.loads(l) for l in open(manifest) if l.strip()]
    work_root = Path(work_root)
    results = []
    for r in rows:
        name = r["design_name"]
        passes = 0
        runs = []
        for k in range(n_repeat):
            res = repair_one(r, work_root / name / f"rep{k}", recall_fn=recall_fn)
            ok = bool(res.get("repaired"))
            passes += int(ok)
            runs.append({
                "k": k, "repaired": ok,
                "patch_range": res.get("patch_range"),
                "err": res.get("error") or res.get("apply_error") or "",
                "used_memory": res.get("used_memory", False),
                "rationale": (res.get("llm_rationale", "") or "")[:140],
            })
            print(f"  [{label}] {name:42} rep{k} {'PASS' if ok else 'fail'}", flush=True)
        rec = {
            "design": name, "top": r["top_module"], "n": n_repeat,
            "passes": passes, "pass_at_1": passes / n_repeat,
            "pass_at_k": int(passes > 0), "runs": runs,
        }
        results.append(rec)
        if out_jsonl:
            with open(out_jsonl, "w") as f:
                for x in results:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")

    n = len(results)
    avg_p1 = sum(x["pass_at_1"] for x in results) / n if n else 0.0
    pk = sum(x["pass_at_k"] for x in results) / n if n else 0.0
    print(f"\n=== {label} ({'with-memory' if recall_fn else 'no-memory'}) | "
          f"cases={n} N={n_repeat} ===")
    print(f"pass@1 (平均单次成功率)     = {avg_p1:.3f}")
    print(f"pass@{n_repeat} (至少一次成功 case 占比) = {pk:.3f}")
    print(f"总成功修复次数 = {sum(x['passes'] for x in results)} / {n * n_repeat}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="L1")
    ap.add_argument("--memory", default=None,
                    help="memory skill 库 jsonl 路径; 给则开 L2(with-memory recall)")
    args = ap.parse_args()

    recall_fn = None
    if args.memory:
        from memory_store import make_recall_fn
        recall_fn = make_recall_fn(args.memory)

    run_batch(args.manifest, args.work, args.n, recall_fn=recall_fn,
              out_jsonl=args.out, label=args.label)


if __name__ == "__main__":
    main()
