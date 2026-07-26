"""蓝方增强消融: 攻克红队 off-by-one 堡垒.

对比 baseline(evidence_k=1, n_candidates=1) vs 增强(evidence_k=6, n_candidates=3),
两配置跑**同一固定毒集**(红队生成一次存 manifest, buggy.v 持久化), 唯一变量=蓝方配置.
重点看 off-by-one/索引偏移子集(co-evolution 暴露的蓝方乏力毒型)修复率提升.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cirfix_adapter import parse_project
from functional_repair import repair_one
from memory_store import make_recall_fn
from red_mutator import generate_repairable_poison


def gen_pool(designs, cirfix_root, per, work, out_manifest):
    rows = []
    for d in designs:
        case = parse_project(Path(cirfix_root) / d / "project.toml")[0]
        got = tries = 0
        while got < per and tries < per * 4:
            p = generate_repairable_poison(case, Path(work) / "gen" / f"{d}_{tries}", design=d)
            tries += 1
            if not p.get("ok"):
                continue
            rows.append({
                "design_name": f"{d}__poison{got}", "top_module": p["top_module"],
                "buggy_rtl": p["buggy_path"], "golden_rtl": p["golden_rtl"],
                "tb_sources": p["tb_sources"], "tb_output": p["tb_output"],
                "deps": p["deps"], "sim_timeout": p["sim_timeout"],
                "mutation_type": p["mutation_type"], "evidence": p.get("evidence", ""),
            })
            got += 1
            print(f"  [gen] {d}__poison{got - 1}: {p['mutation_type']}", flush=True)
    Path(out_manifest).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    return rows


def run_blue(rows, work, memory, evidence_k, n_cand, label):
    recall = make_recall_fn(memory) if memory else None
    res = []
    for r in rows:
        rr = repair_one(r, Path(work) / label / r["design_name"], recall_fn=recall,
                        evidence_k=evidence_k, n_candidates=n_cand)
        res.append({"design": r["design_name"], "mutation": r["mutation_type"],
                    "repaired": bool(rr.get("repaired")), "n_tried": rr.get("n_candidates_tried")})
        print(f"  [{label}] {r['design_name']:26} {r['mutation_type'][:18]:18} "
              f"{'修复' if rr.get('repaired') else '未修':4} (tried {rr.get('n_candidates_tried')})",
              flush=True)
    rate = sum(x["repaired"] for x in res) / len(res) if res else 0
    print(f"  → {label} 修复率 = {rate:.2f} ({sum(x['repaired'] for x in res)}/{len(res)})\n", flush=True)
    return res


def _is_offbyone(mt):
    return any(k in mt for k in ["off-by-one", "off_by_one", "索引", "位宽", "偏移", "边界"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cirfix-root", default="/path/to/cirfix")
    ap.add_argument("--design", action="append", required=True)
    ap.add_argument("--per", type=int, default=3)
    ap.add_argument("--work", required=True)
    ap.add_argument("--memory", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--evidence-k", type=int, default=6)
    ap.add_argument("--n-cand", type=int, default=3)
    args = ap.parse_args()

    work = Path(args.work)
    manifest = work / "poison_pool.jsonl"
    if manifest.exists() and manifest.stat().st_size > 0:
        rows = [json.loads(l) for l in open(manifest) if l.strip()]
        print(f"复用固定毒集 {len(rows)} 条")
    else:
        rows = gen_pool(args.design, args.cirfix_root, args.per, work, manifest)
        print(f"生成固定毒集 {len(rows)} 条 → {manifest}\n")

    base = run_blue(rows, work, args.memory, 1, 1, "baseline")
    enh = run_blue(rows, work, args.memory, args.evidence_k, args.n_cand, "enhanced")

    def rate(res, subset=None):
        s = [x for x in res if subset is None or subset(x["mutation"])]
        return (sum(x["repaired"] for x in s), len(s))

    b_all, e_all = rate(base), rate(enh)
    b_obo, e_obo = rate(base, _is_offbyone), rate(enh, _is_offbyone)
    print("=== 蓝方增强消融 (同一毒集, 唯一变量=蓝方配置) ===")
    print(f"配置: baseline(k=1,n=1) vs enhanced(k={args.evidence_k},n={args.n_cand})")
    print(f"全毒集修复率:     baseline {b_all[0]}/{b_all[1]}={b_all[0]/b_all[1]:.2f}  "
          f"→ enhanced {e_all[0]}/{e_all[1]}={e_all[0]/e_all[1]:.2f}")
    if b_obo[1]:
        print(f"off-by-one 子集:  baseline {b_obo[0]}/{b_obo[1]}={b_obo[0]/b_obo[1]:.2f}  "
              f"→ enhanced {e_obo[0]}/{e_obo[1]}={e_obo[0]/e_obo[1]:.2f}  (红队堡垒)")
    json.dump({"baseline": base, "enhanced": enh,
               "config": {"evidence_k": args.evidence_k, "n_cand": args.n_cand},
               "summary": {"baseline_all": b_all, "enhanced_all": e_all,
                           "baseline_obo": b_obo, "enhanced_obo": e_obo}},
              open(args.out, "w"), ensure_ascii=False, indent=2)
    print(f"\n详细 → {args.out}")


if __name__ == "__main__":
    main()
