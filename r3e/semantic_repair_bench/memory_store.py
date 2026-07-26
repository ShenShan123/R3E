"""功能修复 memory: distill 成功修复经验 → recall(leave-self-out) 注入 prompt.

消融轴: L1=no-memory(recall_fn=None); L2=with-memory(本模块 recall).
transfer 模型: 召回同 family(同设计的其他 bug 变体)已蒸馏的成功修复, 引导当前修复.
leave-self-out: 排除 design_name 完全相同的 skill(防信息泄漏); 同 family 不同 bug 是
合法 within-design transfer — 正是"修过该设计一个 bug 帮修另一个"的 memory 价值.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def family_of(design_name: str) -> str:
    return design_name.split("__")[0]


def distill_from_results(results_jsonl, out_memory) -> list[dict]:
    """从 batch results 蒸馏成功修复 → skill 库(每条成功 run, 按 design+rationale 去重)."""
    skills = []
    seen = set()
    for line in open(results_jsonl):
        if not line.strip():
            continue
        d = json.loads(line)
        for r in d.get("runs", []):
            if not r.get("repaired"):
                continue
            key = (d["design"], (r.get("rationale", "") or "")[:60])
            if key in seen:
                continue
            seen.add(key)
            skills.append({
                "design": d["design"],
                "family": family_of(d["design"]),
                "top": d["top"],
                "patch_range": r.get("patch_range"),
                "rationale": r.get("rationale", ""),
            })
    out_memory = Path(out_memory)
    out_memory.parent.mkdir(parents=True, exist_ok=True)
    with open(out_memory, "w") as f:
        for s in skills:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return skills


def make_recall_fn(memory_path, k: int = 3):
    skills = [json.loads(l) for l in open(memory_path) if l.strip()]
    by_family = defaultdict(list)
    for s in skills:
        by_family[s["family"]].append(s)

    def recall(case: dict) -> str:
        name = case["design_name"]
        fam = family_of(name)
        # 同 family, leave-self-out(排除完全相同 design)
        cands = [s for s in by_family.get(fam, []) if s["design"] != name]
        # 不足补同 top_module 跨设计
        if len(cands) < k:
            for s in skills:
                if (s["top"] == case["top_module"] and s["design"] != name
                        and s not in cands):
                    cands.append(s)
        cands = cands[:k]
        if not cands:
            return ""
        lines = ["", "## 历史成功修复经验（同类设计，仅供参考；当前 bug 可能不同，需独立判断）"]
        for i, s in enumerate(cands, 1):
            lines.append(f"案例{i}[{s['design']}]: 修法 — {s['rationale']}")
        return "\n".join(lines)

    return recall


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sk = distill_from_results(args.results, args.out)
    from collections import Counter
    print(f"distilled {len(sk)} skills from {args.results}")
    print("by family:", dict(Counter(s["family"] for s in sk)))
    print(f"out = {args.out}")
