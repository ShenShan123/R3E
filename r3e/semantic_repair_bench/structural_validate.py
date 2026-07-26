"""补结构性 strategy 验证: 红队生成结构毒(data_flow/state_swap, P5 经验包引导) + G5-style.

P5 co-evolution 暴露蓝方 strategy 只覆盖 constant/off_by_one 二族, 红队越界到结构毒族.
本脚本: 红队生成结构毒 → 加 structural strategy hint(with) vs 不加(base), 严格 N=3 SEM/95%CI,
看新补的结构 strategy 是否同样真提升(复刻 G5 在新 family 上的验证). 闭合 co-evolution 进化循环.
"""
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cirfix_adapter import parse_project
from functional_repair import repair_one
from red_mutator import generate_repairable_poison
from skill_registry import _REPAIR_HINTS, infer_bug_family, make_strategy_recall

S = Path(".iso_semrepair")
CIRFIX = Path("/path/to/cirfix")
N = 3
STRUCT = {"data_flow_error", "state_swap_error"}
DESIGNS = ["sdram_controller", "fsm_full", "lshift_reg", "first_counter_overflow",
           "reed_solomon_decoder"]


def gen_pool(red_ctx):
    def red_recall(d):
        return red_ctx
    pool = []
    for d in DESIGNS:
        try:
            case = parse_project(CIRFIX / d / "project.toml")[0]
        except Exception:  # noqa: BLE001
            continue
        for i in range(6):
            p = generate_repairable_poison(case, S / "struct" / f"{d}_{i}",
                                           red_recall_fn=red_recall, design=d)
            if not p.get("ok"):
                continue
            c = {"design_name": f"{d}__s{i}", "top_module": p["top_module"],
                 "buggy_rtl": p["buggy_path"], "golden_rtl": p["golden_rtl"],
                 "tb_sources": p["tb_sources"], "tb_output": p["tb_output"],
                 "deps": p["deps"], "sim_timeout": p["sim_timeout"],
                 "mutation_type": p["mutation_type"]}
            if infer_bug_family(c) in STRUCT:
                pool.append(c)
                print(f"  + {d} {infer_bug_family(c)} ({p['mutation_type'][:16]}) total={len(pool)}",
                      flush=True)
    return pool


def run(pool, rep, ws, sr):
    ok = 0
    for c in pool:
        rf = sr if ws else None
        tag = "with" if ws else "base"
        r = repair_one(c, S / "structexp" / f"{tag}_r{rep}" / c["design_name"],
                       recall_fn=rf, evidence_k=6, n_candidates=3)
        ok += bool(r.get("repaired"))
    return ok / len(pool)


def main():
    print(f"structural hints 已注入: {[k for k in _REPAIR_HINTS if k in STRUCT]}", flush=True)
    pack = [json.loads(l) for l in open(S / "p5_coevolve/red_pack.jsonl") if l.strip()]
    red_ctx = "\n".join(p.get("strategy", "")[:120] for p in pack)[:800]
    pool = gen_pool(red_ctx)
    json.dump([dict(c) for c in pool], open(S / "struct_pool.json", "w"), ensure_ascii=False)
    print(f"\n结构毒池: {len(pool)}\n", flush=True)
    if len(pool) < 4:
        print("结构毒太少(<4), 退出——需调红队引导或扩 design")
        return
    sr = make_strategy_recall()
    out = {}
    for label, ws in [("base_no_strategy", False), ("with_struct_strategy", True)]:
        rates = [run(pool, rep, ws, sr) for rep in range(N)]
        out[label] = (statistics.mean(rates),
                      statistics.stdev(rates) if len(rates) > 1 else 0.0, rates)
        print(f"  {label}: {out[label][0]:.3f}±{out[label][1]:.3f} "
              f"{[round(r,3) for r in rates]}", flush=True)
        json.dump({k: list(v) for k, v in out.items()},
                  open(S / "struct_strategy.json", "w"), ensure_ascii=False, indent=2)
    b, g = out["base_no_strategy"], out["with_struct_strategy"]
    d = g[0] - b[0]
    sem = math.sqrt((b[1] / math.sqrt(N)) ** 2 + (g[1] / math.sqrt(N)) ** 2)
    ci = 1.96 * sem
    print(f"\n=== 结构 strategy 验证 (n={len(pool)} 结构毒, SEM/95%CI) ===")
    print(f"  base {b[0]:.3f} → with_strategy {g[0]:.3f}, Δ{d*100:+.1f}pp 95%CI±{ci*100:.1f} "
          f"[{(d-ci)*100:+.1f},{(d+ci)*100:+.1f}] → {'✓ 显著(结构 strategy 有效)' if d > ci else '≈未显著'}")


if __name__ == "__main__":
    main()
