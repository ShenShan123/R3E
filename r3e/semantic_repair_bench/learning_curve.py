"""A 阶段 learning curve: 无 tb 的 external RTL 池上, 蓝方"修的 bug 越多越强"曲线涌现.

formal 判(golden 自作 oracle, 无需 tb). within-design transfer(同设计不同毒): test 毒集固定,
train 持续造新毒→蓝修(memory recall)→formal 判→成功 distill, 每 N train 测 test 修复率,
看 test 修复率随 memory 累积单调上升 = 蓝方越修越强(in-the-wild self-evolution).
"""
import json
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from formal_gate import formal_judge
from functional_repair import apply_block, propose
from memory_store import make_recall_fn
from red_mutator import mutate_once

EB = Path("/path/to/rtl-data/external_benchmarks_nangate45_expand_success_rtl_2026-04-12")
S = Path(".iso_semrepair")
W = S / "lcurve"
FT = 40  # formal timeout/case


def golden_of(d):
    base = EB / d
    vs = list((base / "rtl").glob("*.v")) or list(base.rglob("*.v"))
    vs = [v for v in vs if "_tb" not in v.name and "test" not in v.name.lower()]
    if not vs:
        return None
    g = vs[0]
    m = re.search(r"^\s*module\s+(\w+)", g.read_text(errors="ignore"), re.M)
    if not m:
        return None
    return str(g), m.group(1), [str(v) for v in vs if v != g]


def build_pool(n_target=8, max_lines=350):
    pool = []
    for d in sorted(os.listdir(EB))[:50]:
        if not (EB / d).is_dir():
            continue
        go = golden_of(d)
        if not go:
            continue
        g, top, deps = go
        if Path(g).read_text(errors="ignore").count("\n") > max_lines:
            continue
        j = formal_judge(g, deps, g, top, W / f"self_{d}", timeout=FT)  # self-equiv 筛
        if j.equiv:
            pool.append((d, g, top, deps))
            print(f"  pool+ {d} (proven {j.proven}/{j.total})", flush=True)
        if len(pool) >= n_target:
            break
    return pool


def gen_poison(d, g, top, deps, wd):
    """red 造一条 formal-可修毒(buggy NOT equiv golden)."""
    for t in range(4):
        m = mutate_once(g, wd / f"t{t}")
        if "error" in m:
            continue
        j = formal_judge(g, deps, m["buggy_path"], top, wd / f"j{t}", timeout=FT)
        if not j.equiv:  # NOT equiv = 真功能 bug
            return {"design": d, "golden": g, "top": top, "deps": deps,
                    "buggy": m["buggy_path"], "mut": m["mutation_type"]}
    return None


def blue(p, wd, recall):
    ctx = recall({"design_name": p["design"], "top_module": p["top"]}) if recall else ""
    prop = propose(p["buggy"], "该模块功能与正确电路不符(formal 判不等价), 请分析 RTL 定位并修复 bug", ctx)
    if isinstance(prop, list):
        prop = prop[0] if prop else {}
    if not isinstance(prop, dict) or "start_line" not in prop:
        return False, "", None
    try:
        patched = apply_block(p["buggy"], int(prop["start_line"]), int(prop["end_line"]),
                              prop["new_code"], wd / "patched.v")
    except Exception:  # noqa: BLE001
        return False, "", None
    j = formal_judge(p["golden"], p["deps"], patched, p["top"], wd / "jp", timeout=FT)
    return j.equiv, prop.get("rationale", ""), [prop.get("start_line"), prop.get("end_line")]


def main():
    W.mkdir(parents=True, exist_ok=True)
    print("=== 筛 formal-proven 设计池 ===", flush=True)
    pool = build_pool()
    print(f"设计池 {len(pool)}: {[d for d,_,_,_ in pool]}", flush=True)
    if len(pool) < 3:
        print("池太小, 退出"); return

    # test 毒集(固定, 每设计 4 毒, 降统计噪声)
    test = []
    for d, g, top, deps in pool:
        for k in range(4):
            p = gen_poison(d, g, top, deps, W / f"test_{d}_{k}")
            if p:
                test.append(p)
    print(f"test 毒集 {len(test)} 条", flush=True)

    def eval_avg(mem_path, n=2):  # 多次 eval 取平均, 降 LLM 非确定性噪声
        recall = make_recall_fn(mem_path) if Path(mem_path).stat().st_size > 0 else None
        rates = []
        for run in range(n):
            ok = sum(blue(p, W / f"eval_{run}_{i}", recall)[0] for i, p in enumerate(test))
            rates.append(ok / len(test))
        return round(sum(rates) / len(rates), 3)

    mem = W / "mem.jsonl"
    mem.write_text("")
    train = [(d, g, top, deps) for d, g, top, deps in pool for _ in range(6)]
    random.seed(0)
    random.shuffle(train)
    EVAL_EVERY = 10

    curve = [{"train": 0, "mem": 0, "test_pass": eval_avg(str(mem))}]
    print(f"R0(mem空): test_pass={curve[-1]['test_pass']}", flush=True)
    for i, (d, g, top, deps) in enumerate(train, 1):
        p = gen_poison(d, g, top, deps, W / f"train_{i}")
        if p:  # 造毒成功才修+distill
            recall = make_recall_fn(str(mem)) if mem.stat().st_size > 0 else None
            ok, rat, pr = blue(p, W / f"trb_{i}", recall)
            if ok:
                with open(mem, "a") as f:
                    f.write(json.dumps({"design": p["design"], "family": p["design"],
                                        "top": p["top"], "patch_range": pr, "rationale": rat},
                                       ensure_ascii=False) + "\n")
        if i % EVAL_EVERY == 0:  # ★ eval 独立于造毒成败(修 bug: 原 continue 会吞掉 eval)
            tr = eval_avg(str(mem))
            ms = sum(1 for _ in open(mem))
            curve.append({"train": i, "mem": ms, "test_pass": tr})
            print(f"train {i}(mem {ms}): test_pass={tr}", flush=True)
            json.dump(curve, open(S / "lcurve.json", "w"), indent=2)

    print("\n=== learning curve (蓝方越修越强) ===", flush=True)
    for c in curve:
        print(f"  train={c['train']:2} mem={c['mem']:2} test_pass={c['test_pass']:.3f}")
    json.dump(curve, open(S / "lcurve.json", "w"), indent=2)


if __name__ == "__main__":
    main()
