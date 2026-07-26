"""ablation 轴4 red-curriculum: 构建两个蓝 memory(唯一差异=训练毒有无红队 curriculum).

- random_mem: red_mutator 无 red_recall(随机变异, 无 curriculum) → 蓝修(no mem) → distill 成功.
- curr_mem:   red_mutator + 累积 red_memory(critic 升级, 多样深化 curriculum) → 蓝修 → distill.
同设计池、等量目标, 看 curriculum 训练的 memory 是否帮下游修复更好(论证红队价值).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import red_memory
from cirfix_adapter import parse_project
from functional_repair import repair_one
from red_critic import critique
from red_mutator import generate_repairable_poison

CF = Path("/path/to/cirfix")
DESIGNS = [  # 复杂/中等设计(有 transfer 空间)
    "fsm_full", "lshift_reg", "first_counter_overflow", "mux_4_1",
    "decoder_3_to_8", "opencores/reed_solomon_decoder",
]


def _blue_skill(out_mem, design, top, res):
    skill = {"design": design, "family": design.split("/")[-1].split("__")[0], "top": top,
             "patch_range": res.get("patch_range"), "rationale": res.get("llm_rationale", "")}
    with open(out_mem, "a") as f:
        f.write(json.dumps(skill, ensure_ascii=False) + "\n")


def build(mode, out_mem, work, per_design=3):
    """mode='random'(无 curriculum) | 'curriculum'(red_recall 升级). distill 蓝修成功 → out_mem."""
    Path(out_mem).write_text("")
    red_mem = Path(work) / "red_mem.jsonl"
    Path(work).mkdir(parents=True, exist_ok=True)
    red_mem.write_text("")
    n_distilled = 0
    for d in DESIGNS:
        case = parse_project(CF / d / "project.toml")[0]
        dname = d.split("/")[-1]
        for i in range(per_design):
            recall = red_memory.make_red_recall(str(red_mem)) if mode == "curriculum" else None
            p = generate_repairable_poison(case, Path(work) / f"{dname}_{i}" / "red",
                                           red_recall_fn=recall, design=dname)
            if not p.get("ok"):
                continue
            blue_case = {"design_name": f"{dname}__p{i}", "top_module": p["top_module"],
                         "buggy_rtl": p["buggy_path"], "golden_rtl": p["golden_rtl"],
                         "tb_sources": p["tb_sources"], "tb_output": p["tb_output"],
                         "deps": p["deps"], "sim_timeout": p["sim_timeout"]}
            res = repair_one(blue_case, Path(work) / f"{dname}_{i}" / "blue",
                             evidence_k=6, n_candidates=3)
            blue_rep = bool(res.get("repaired"))
            if blue_rep:
                _blue_skill(out_mem, f"{dname}__p{i}", p["top_module"], res)
                n_distilled += 1
            if mode == "curriculum":  # 累积红记忆(critic 评估 → 下次升级)
                crit = critique(p["golden_rtl"], p["buggy_path"], p["mutation_type"],
                                p.get("range"), blue_rep, p.get("tries"))
                red_memory.update(str(red_mem), {
                    "design": dname, "mutation_type": p["mutation_type"],
                    "verdict": crit["verdict"], "reason": crit.get("reason", ""),
                    "upgrade_hint": crit.get("upgrade_hint", "")})
            print(f"  [{mode}] {dname}_{i}: poison={p['mutation_type'][:14]} blue={'修' if blue_rep else '未'}", flush=True)
    print(f"[{mode}] distilled {n_distilled} skills → {out_mem}", flush=True)
    return n_distilled


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["random", "curriculum"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--per-design", type=int, default=3)
    args = ap.parse_args()
    build(args.mode, args.out, args.work, args.per_design)
