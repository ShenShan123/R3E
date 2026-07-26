"""泛化 memory（接回原有蓝方工具链泛化机制 distill_frontend schema 精神）.

区别于简化版 memory_store(按 design 存 + 按 family 检索, cross-family 召空):
- distill: 按 **bug 类型(error_signature)** 泛化入库, repair_strategy 抽象修复策略(原有 schema).
- recall: **按 bug 类型跨设计召回**同类修复策略(不按 family) → cross-family 也能召回同类 bug 经验.
对应原有 distill_frontend 的 precondition.error_signature + action_template.repair_strategy 泛化导向.
"""
from __future__ import annotations

import json
from pathlib import Path

# bug 类型分类(关键词 → error_signature, 泛化不绑定具体设计)
BUG_CLASSES = {
    "constant_error": ["常量", "字面量", "constant", "取值", "8'b", "4'b", "改为正确"],
    "operator_error": ["算子", "运算符", "operator", "翻转", "逐位", "归约", "异或", "与或"],
    "off_by_one": ["off-by-one", "索引", "位宽", "偏移", "边界", "减1", "加1", "次高位", "index"],
    "condition_error": ["条件", "分支", "case", "交换", "if", "优先级", "比较"],
    "reset_error": ["复位", "reset", "清零", "初始", "rst"],
    "assignment_error": ["赋值", "遗漏", "非阻塞", "阻塞", "敏感列表", "posedge"],
    "state_error": ["状态", "state", "fsm", "gnt", "grant", "跳转"],
}


def classify_bug(rationale: str) -> str:
    r = (rationale or "").lower()
    scores = {bc: sum(1 for kw in kws if kw.lower() in r) for bc, kws in BUG_CLASSES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def distill_generalized(results_jsonl, out_memory, families=None) -> list[dict]:
    """成功修复 → 泛化 skill(error_signature=bug_class, repair_strategy=泛化策略). 用原有 schema 结构.

    families: 若给, 只蒸馏这些 design family 的修复(cross-family split 用).
    """
    skills, seen = [], set()
    for line in open(results_jsonl):
        if not line.strip():
            continue
        d = json.loads(line)
        fam = d["design"].split("__")[0]
        if families and fam not in families:
            continue
        for r in d.get("runs", []):
            if not r.get("repaired"):
                continue
            rat = r.get("rationale", "") or ""
            bc = classify_bug(rat)
            key = (bc, rat[:50])
            if key in seen:
                continue
            seen.add(key)
            skills.append({  # 原有 distill_frontend schema 结构
                "skill_name": f"frontend_functional_repair_{bc}",
                "precondition": {"error_signature": bc, "context_pattern": rat[:90]},
                "action_template": {"repair_strategy": rat, "bug_class": bc},
                "validation": {"frontend_metric": "testbench_differential_pass"},
                "design": d["design"], "family": fam,  # 出处(不参与检索)
            })
    Path(out_memory).parent.mkdir(parents=True, exist_ok=True)
    Path(out_memory).write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in skills))
    return skills


def make_recall_fn_generalized(memory_path, k: int = 4):
    """按 bug 类型泛化召回(跨设计, 不按 family): 召回多样 bug_class 代表策略作可迁移经验库."""
    if not Path(memory_path).exists() or Path(memory_path).stat().st_size == 0:
        return lambda case=None: ""
    skills = [json.loads(l) for l in open(memory_path) if l.strip()]
    by_class: dict[str, list] = {}
    for s in skills:
        by_class.setdefault(s["action_template"]["bug_class"], []).append(s)

    def recall(case: dict) -> str:
        name = case.get("design_name", "")
        # 召回多样 bug_class 代表(每类前 2), 跨设计; leave-self-out 排同 design
        cands = []
        for bc, ss in by_class.items():
            cands.extend([s for s in ss if s["design"] != name][:2])
        cands = cands[:k]
        if not cands:
            return ""
        lines = ["", "## 历史修复策略库（按 bug 类型泛化，跨设计可迁移；当前 bug 需独立判断）"]
        for i, s in enumerate(cands, 1):
            lines.append(f"策略{i}[{s['action_template']['bug_class']}]: "
                         f"{s['action_template']['repair_strategy'][:85]}")
        return "\n".join(lines)

    return recall
