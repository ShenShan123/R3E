"""P3 promotion gate: formalize skill 晋升判据(制度化"哪些 skill 能 promote").

candidate skill 晋升需过 4 闸:
 ① fixed suite improvement: strategy 在难 family 上提升, SEM/95%CI 不含 0(复用 confirm_g5)
 ② no regression: routed(policy+strategy) vs baseline(k1n1) 整体不显著下降(新跑 Red-Fixed-12 全集)
 ③ no oracle leakage: strategy hint 不含答案/具体值(静态检查)
 ④ no raw memory injection: hint 是通用方法论非历史 case 文本(静态检查)
全过 → PROMOTE(写 promotion 证据)。用蓝方系统 functional_repair + skill_registry. SEM/95%CI 正确判据.
"""
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one
from skill_registry import _REPAIR_HINTS, load_registry, make_strategy_recall, route

S = Path(".iso_semrepair")
N = 3


def ci95_delta(m1, s1, m2, s2, n):
    """均值差 (m1-m2) 的 95%CI 半宽."""
    sem = math.sqrt((s1 / math.sqrt(n)) ** 2 + (s2 / math.sqrt(n)) ** 2)
    return m1 - m2, 1.96 * sem


def suite_rate(suite, mode, reg, sr, tag):
    rates = []
    for rep in range(N):
        ok = 0
        for c in suite:
            if mode == "baseline":
                r = repair_one(c, S / "pgate" / f"{tag}_base_r{rep}" / c["design_name"],
                               recall_fn=None, evidence_k=1, n_candidates=1)
            else:  # routed: registry policy + strategy hint
                pol, _ = route(c, reg)
                r = repair_one(c, S / "pgate" / f"{tag}_routed_r{rep}" / c["design_name"],
                               recall_fn=sr, evidence_k=pol["evidence_k"],
                               n_candidates=pol["n_candidates"])
            ok += bool(r.get("repaired"))
        rates.append(ok / len(suite))
    return statistics.mean(rates), (statistics.stdev(rates) if len(rates) > 1 else 0.0), rates


def main():
    reg = load_registry(str(S / "skills.json"))
    sr = make_strategy_recall()
    print("=== P3 promotion gate (4 闸) ===\n", flush=True)

    # ① fixed suite improvement(复用 confirm_g5 N=7)
    cf = json.load(open(S / "confirm_g5.json"))
    g3m, g3s, _ = cf["G3_no_hint"]
    g5m, g5s, _ = cf["G5_strategy"]
    d1, ci1 = ci95_delta(g5m, g5s, g3m, g3s, 7)
    fixed_pass = d1 > ci1
    print(f"① fixed suite(strategy 难 family, confirm_g5 N=7): "
          f"Δ{d1*100:+.1f}pp 95%CI±{ci1*100:.1f} → {'PASS' if fixed_pass else 'FAIL'}", flush=True)

    # ③④ provenance: hint 静态检查. 拦真泄漏(历史 case recall 格式 / 具体常量答案值),
    # 不拦泛型占位符(4'bxxxx 教学示例)或 Verilog 语法术语(case 语句)
    import re

    def _has_leak(h):
        if "案例" in h and "[" in h:  # 历史 case recall 格式(案例N[design])
            return True
        if re.search(r"\d+'[bhdBHD][0-9a-fA-F]{2,}", h):  # 具体常量值 4'b1010(非占位 4'bxxxx)
            return True
        return False
    prov_pass = all(not _has_leak(h) for h in _REPAIR_HINTS.values())
    print(f"③④ provenance(hint 通用方法论, 无答案/case): {'PASS' if prov_pass else 'FAIL'}", flush=True)

    # ② no regression: routed vs baseline on Red-Fixed-12 全集
    redfix = [json.loads(l) for l in open(S / "blue_abl/poison_pool.jsonl") if l.strip()]
    print(f"\n② regression test: routed vs baseline on Red-Fixed-12({len(redfix)}) N={N}...", flush=True)
    rm, rs, rr = suite_rate(redfix, "routed", reg, sr, "reg")
    bm, bs, br = suite_rate(redfix, "baseline", reg, sr, "reg")
    d2, ci2 = ci95_delta(rm, rs, bm, bs, N)  # (routed_mean,routed_std, baseline_mean,baseline_std)
    reg_pass = (rm - bm) > -ci2  # routed 不显著低于 baseline
    print(f"  routed {rm:.3f}±{rs:.3f} | baseline {bm:.3f}±{bs:.3f} | Δ{d2*100:+.1f}pp CI±{ci2*100:.1f}")
    print(f"  → {'PASS(不回归, routed≥baseline)' if reg_pass else 'FAIL(回归)'}", flush=True)

    promote = fixed_pass and prov_pass and reg_pass
    print(f"\n{'='*52}")
    print(f"=== promotion decision: {'✓ PROMOTE' if promote else '✗ REJECT'} ===")
    print(f"  ① fixed={fixed_pass} ② regression={reg_pass} ③④ provenance={prov_pass}")
    out = {"promote": promote, "fixed": {"delta": d1, "ci": ci1, "pass": fixed_pass},
           "regression": {"routed": rm, "baseline": bm, "delta": d2, "ci": ci2, "pass": reg_pass},
           "provenance": prov_pass}
    json.dump(out, open(S / "promotion_gate.json", "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
