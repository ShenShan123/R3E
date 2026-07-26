#!/usr/bin/env python3
"""
generalize_skills.py — 技能泛化：从具体设计提取通用拓扑法则
"""
import json
from pathlib import Path

SKILL_INDEX_FILE = Path(".micro_surgeon_memory/skill_index.jsonl")
GENERALIZED_INDEX_FILE = Path(".micro_surgeon_memory/generalized_skill_index.jsonl")

# 定义拓扑特征映射字典
TOPOLOGY_MAP = {
    "c1908": "deep_multiplier_tree",
    "c1355": "high_fanout_mux_logic",
    "c3540": "complex_alu_control",
    "c880":  "shallow_alu_datapath",
    "c432":  "priority_decoder_logic"
}

def generalize():
    if not SKILL_INDEX_FILE.exists():
        print("未找到基础技能库！")
        return

    generalized_skills = []
    seen_hashes = set()

    with open(SKILL_INDEX_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                skill = json.loads(line)
            except:
                continue

            # 提取原设计名称
            origin_design = None
            for design in TOPOLOGY_MAP.keys():
                if design in skill.get("precondition", {}).get("error_signature", ""):
                    origin_design = design
                    break

            if not origin_design:
                continue

            topology_pattern = TOPOLOGY_MAP[origin_design]
            strategy = skill.get("action_template", {}).get("repair_strategy", "unknown")
            delta = skill.get("metadata", {}).get("distilled_from_delta", 0.0)

            # 只有改善量足够大的"优质技能"才值得被泛化提取
            if delta < 0.05 and strategy != "composite":
                continue

            new_skill_name = f"universal_{topology_pattern}_{strategy}"

            if new_skill_name in seen_hashes:
                continue
            seen_hashes.add(new_skill_name)

            # 创建泛化后的技能
            gen_skill = {
                "skill_name": new_skill_name,
                "precondition": {
                    "error_signature": "WNS < 0",
                    "context_pattern": f"critical path contains {topology_pattern}"
                },
                "action_template": {
                    "repair_strategy": strategy,
                    "allowed_edit_scope": ["*.v", "*_synth.v"]
                },
                "validation": skill.get("validation", {"backend_metric": "WNS_improvement > 0"}),
                "rollback_condition": {"wns_degradation": True},
                "metadata": {
                    "origin_skill": skill.get("skill_name", "unknown"),
                    "topology_abstraction": True,
                    "confidence_score": min(0.95, delta * 10)
                }
            }
            generalized_skills.append(gen_skill)

    # 写入新的通用技能库
    with open(GENERALIZED_INDEX_FILE, 'w', encoding='utf-8') as f:
        for s in generalized_skills:
            f.write(json.dumps(s, ensure_ascii=False) + '\n')

    print(f"🎉 成功将底层物理经验抽象为 {len(generalized_skills)} 条通用拓扑法则！")
    print(f"   输出文件: {GENERALIZED_INDEX_FILE}")

if __name__ == "__main__":
    generalize()
