"""红蓝对抗最小闭环: 红队生可修毒 → 蓝队(with memory)修 → 判据闸判. 存在性证明.

红毒是 CirFix 39 case 之外的**新** bug(curriculum 扩展); 蓝方 recall 同 family 的 memory
经验尝试修复; 判据闸(differential)裁决. 闭环=红生→蓝修→判, 全 by-construction 可修.
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


def loop_one(toml, work_dir, memory_path=None) -> dict:
    wd = Path(work_dir)
    case = parse_project(toml)[0]
    design = Path(toml).parent.name

    # ── 红队: 生可修毒 ──
    poison = generate_repairable_poison(case, wd / "red")
    if not poison.get("ok"):
        return {"design": design, "stage": "red", "ok": False, "reason": poison["reason"]}

    # ── 转蓝方 case (红毒作 buggy) ──
    blue_case = {
        "design_name": f"{design}__redpoison",
        "top_module": poison["top_module"],
        "buggy_rtl": poison["buggy_path"],
        "golden_rtl": poison["golden_rtl"],
        "tb_sources": poison["tb_sources"], "tb_output": poison["tb_output"],
        "deps": poison["deps"], "sim_timeout": poison["sim_timeout"],
    }

    # ── 蓝队: with-memory 修复 ──
    recall_fn = make_recall_fn(memory_path) if memory_path else None
    res = repair_one(blue_case, wd / "blue", recall_fn=recall_fn)

    return {
        "design": design,
        "red_mutation": poison["mutation_type"],
        "red_rationale": poison["rationale"],
        "red_evidence": poison["evidence"],
        "blue_used_memory": res.get("used_memory", False),
        "blue_rationale": res.get("llm_rationale", ""),
        "blue_repaired": bool(res.get("repaired")),
        "blue_patched_evidence": res.get("patched_evidence", ""),
        "blue_error": res.get("error") or res.get("apply_error") or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cirfix-root", default="/path/to/cirfix")
    ap.add_argument("--design", action="append", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--memory", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    results = []
    for d in args.design:
        toml = Path(args.cirfix_root) / d / "project.toml"
        r = loop_one(str(toml), Path(args.work) / d, memory_path=args.memory)
        results.append(r)
        if r.get("ok") is False:
            print(f"[{d}] 红队失败: {r['reason']}")
            continue
        tag = "蓝修✓" if r["blue_repaired"] else "蓝未修复"
        print(f"[{d}] 红毒({r['red_mutation']}) → {tag} "
              f"[mem={r['blue_used_memory']}]")
        print(f"     红: {r['red_rationale'][:75]}")
        print(f"     蓝: {r['blue_rationale'][:75]}")
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results))
    rep = sum(1 for r in results if r.get("blue_repaired"))
    print(f"\n=== 红蓝闭环: {len(results)} 轮 | 蓝修成功 {rep} ===")


if __name__ == "__main__":
    main()
