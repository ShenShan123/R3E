"""全集自检: 每 bug case 跑 golden(应PASS) + buggy(应FAIL), 分类筛有效 cases.

分类: functional(golden PASS+buggy compare-mismatch, 新主战场) / syntax(buggy 编译错, 现有
F0 链) / golden_fail(golden 跑不了→case 对本 harness 无效, 如 CSV-only/多文件/超时) /
buggy_passes(bug 不影响该 tb 输出) / no_verilog_tb(只有 CSV testbench, 本期不接).
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from oracle_gate import judge


def main():
    rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
    wd = Path(".iso_semrepair/survey")
    wd.mkdir(parents=True, exist_ok=True)
    cat = Counter()
    valid = []

    for r in rows:
        name = r["design_name"]
        if not r.get("tb_output") or not r.get("tb_sources"):
            cat["no_verilog_tb"] += 1
            continue
        g = judge(r, r["golden_rtl"], wd / name / "g", timeout=40)
        if not g.ok:
            cat["golden_fail"] += 1
            print(f"GOLDEN_FAIL  {name:34} {g.stage:10} {(g.err or g.mismatch)[:55]}", flush=True)
            continue
        b = judge(r, r["buggy_rtl"], wd / name / "b", timeout=40)
        if b.ok:
            cat["buggy_passes"] += 1
            print(f"BUG_NOEFFECT {name:34}", flush=True)
            continue
        if b.stage == "compare":
            cat["functional"] += 1
            valid.append(r)
            print(f"FUNCTIONAL   {name:34} evidence={b.mismatch[:40]}", flush=True)
        elif b.stage == "cand_sim":
            cat["syntax"] += 1
            print(f"SYNTAX       {name:34} {b.err[:40]}", flush=True)

    print("\n=== 分类 ===", dict(cat))
    print(f"有效功能型 cases = {cat['functional']} (L1/L2 消融池)")
    json.dump([r["design_name"] for r in valid], open(wd / "valid_functional_names.json", "w"))
    with open(wd / "valid_functional.jsonl", "w") as f:
        for r in valid:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"valid manifest → {wd}/valid_functional.jsonl")


if __name__ == "__main__":
    main()
