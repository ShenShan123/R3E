"""ablation 轴5 correctness-gate: 强 gate(testbench) vs 弱 gate(compile-only) 对 self-evolution.

同一 random 毒集, 蓝修一次, 双 distill:
- strong_mem: 只 distill testbench-pass 的修复(correctness gate 把关, memory 干净).
- weak_mem:   distill 任一**编译过**的候选(含 testbench-fail 的假修复, gate 形同虚设, memory 污染).
论证: 无 correctness gate, self-evolution 把假修复(编译过但功能错)当经验, memory 退化.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cirfix_adapter import parse_project
from functional_repair import repair_one

CF = Path("/path/to/cirfix")
DESIGNS = ["fsm_full", "lshift_reg", "first_counter_overflow", "mux_4_1",
           "decoder_3_to_8", "opencores/reed_solomon_decoder"]


def _skill(mem, design, top, rationale, patch_range):
    s = {"design": design, "family": design.split("/")[-1].split("__")[0], "top": top,
         "patch_range": patch_range, "rationale": rationale}
    with open(mem, "a") as f:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")


def _compiled(c):
    """候选是否编译过(apply 成功 + patched_evidence 非 compile_err)."""
    if not c.get("patch_range") or c.get("error") or c.get("apply_error"):
        return False
    ev = str(c.get("patched_evidence", ""))
    return not ev.startswith("compile") and "compile_err" not in ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strong-out", required=True)
    ap.add_argument("--weak-out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--per-design", type=int, default=3)
    args = ap.parse_args()
    from red_mutator import generate_repairable_poison

    Path(args.strong_out).write_text("")
    Path(args.weak_out).write_text("")
    n_strong = n_weak = n_false = 0
    for d in DESIGNS:
        case = parse_project(CF / d / "project.toml")[0]
        dname = d.split("/")[-1]
        for i in range(args.per_design):
            p = generate_repairable_poison(case, Path(args.work) / f"{dname}_{i}" / "red", design=dname)
            if not p.get("ok"):
                continue
            blue_case = {"design_name": f"{dname}__p{i}", "top_module": p["top_module"],
                         "buggy_rtl": p["buggy_path"], "golden_rtl": p["golden_rtl"],
                         "tb_sources": p["tb_sources"], "tb_output": p["tb_output"],
                         "deps": p["deps"], "sim_timeout": p["sim_timeout"]}
            res = repair_one(blue_case, Path(args.work) / f"{dname}_{i}" / "blue",
                             evidence_k=6, n_candidates=3)
            cands = res.get("candidates", [])
            # strong gate: testbench-pass 才入库
            if res.get("repaired"):
                win = next((c for c in cands if c.get("patched_ok")), None)
                _skill(args.strong_out, f"{dname}__p{i}", p["top_module"],
                       win.get("rationale", ""), win.get("patch_range"))
                n_strong += 1
            # weak gate: 任一编译过的候选入库(含假修复)
            wc = next((c for c in cands if _compiled(c)), None)
            if wc:
                _skill(args.weak_out, f"{dname}__p{i}", p["top_module"],
                       wc.get("rationale", ""), wc.get("patch_range"))
                n_weak += 1
                if not wc.get("patched_ok"):
                    n_false += 1  # 假修复(编译过但功能错)
            print(f"  {dname}_{i}: strong={'Y' if res.get('repaired') else 'N'} "
                  f"weak={'Y' if wc else 'N'}{'(假)' if wc and not wc.get('patched_ok') else ''}", flush=True)
    print(f"\nstrong_mem={n_strong} skills | weak_mem={n_weak} skills (其中假修复 {n_false})", flush=True)


if __name__ == "__main__":
    main()
