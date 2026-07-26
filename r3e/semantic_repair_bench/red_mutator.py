"""受约束红队: 语义级**可修毒**生成器(动态测试器, 非破坏者).

操作者约束: 毒必须是能让 LLM 想办法修复的语义级 bug, 不是一味加紧时钟/改 config 导致
耗尽物理层动作也无法修复. 故:
  - 投毒域 = 仅语义/功能(算子翻转/索引偏移/常量错/条件交换/赋值漏), 不碰 timing/SDC/config.
  - by-construction 可修: 毒=对 golden RTL 的一处语义变异; golden 本身即"修复存在性证明"
    (revert 到 golden 必通过 testbench). 复用 oracle_gate 作可修性证书.
  - 可修性闸三条: ① buggy 能编译(非语法错) ② buggy testbench-FAIL(真功能 bug, 非空变异)
    ③ golden testbench-PASS(修复必存在). 三条全过才是合法可修毒, 否则拒绝重试.

红队=多样可修 bug 的 curriculum 生成器, 扩 CirFix 固定 39 case 之外, 维持蓝方 memory 爬升斜率
(对应单数据集 within-design 饱和的发现).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import apply_block, propose  # noqa: E402 — 复用 apply_block + call_llm
from oracle_gate import judge  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microsurgeon_flow.backend_eco_oneshot import call_llm  # noqa: E402

_MUTATION_TYPES = ["算子翻转(& vs |, + vs -, < vs <=)", "索引/位宽偏移(off-by-one)",
                   "常量错误(改一个字面量)", "条件分支交换(if/case 条件或赋值互换)",
                   "敏感列表/赋值遗漏(漏一个信号或赋值)",
                   # 结构毒族(P5 co-evolution 暴露, 纳入红队 curriculum 标准毒库)
                   "数据流反向(移位方向 << vs >>, 或赋值源与目标互换)",
                   "状态信号互换(当前态与下一态信号互换, 利用非阻塞延迟)"]

_MUT_PROMPT = """你是硬件测试的红队，任务是对一个**功能正确**的 Verilog 模块注入**一处**隐蔽的
**语义/功能 bug**，用于动态测试下游修复器。严格遵守约束。

## 正确 RTL（golden，带行号）
{rtl}

## 硬约束
1. 只注入**语义/功能** bug（候选类型：{mtypes}）。
2. **禁止** timing/SDC/时钟/config 类改动，**禁止**单纯语法错（必须能编译）。
3. 改动必须**改变功能行为**（让 testbench 失败），但保持**可编译**、可被人理解和修复。
4. 像真实工程笔误（可以是一处，也可按下方经验组合/深化）。
{red_memory}
## 输出严格 JSON：
{{"start_line": <起,1-based>, "end_line": <止,闭区间>, "new_code": "<注入 bug 后替换这些行的代码,不带行号>", "mutation_type": "<上述类型之一>", "rationale": "<≤2句:注入了什么 bug、为何能编译但功能错>"}}
"""


def _numbered(text: str) -> str:
    return "\n".join(f"{i}: {ln}" for i, ln in enumerate(text.splitlines(), 1))


def mutate_once(golden_rtl, work_dir, red_ctx: str = "") -> dict:
    """LLM 对 golden 注入语义 bug → 返回 {buggy_path, mutation_type, rationale} 或 error.

    red_ctx: 红队记忆注入(避开弱毒/按 upgrade 升级深化), ""=无记忆(首轮).
    """
    rtl = Path(golden_rtl).read_text(errors="ignore")
    out = call_llm(_MUT_PROMPT.format(rtl=_numbered(rtl), mtypes="; ".join(_MUTATION_TYPES),
                                      red_memory=red_ctx))
    if isinstance(out, list):
        out = next((x for x in out if isinstance(x, dict)), None) or {"llm_call_error": "list"}
    if not isinstance(out, dict) or "llm_call_error" in out:
        return {"error": out.get("llm_call_error", "bad_format") if isinstance(out, dict) else "bad_format"}
    try:
        buggy = apply_block(golden_rtl, int(out["start_line"]), int(out["end_line"]),
                            out["new_code"], Path(work_dir) / "buggy.v")
    except Exception as e:  # noqa: BLE001
        return {"error": f"apply_failed: {e}"}
    return {"buggy_path": str(buggy), "mutation_type": out.get("mutation_type", ""),
            "rationale": out.get("rationale", ""),
            "range": [out.get("start_line"), out.get("end_line")]}


def generate_repairable_poison(case: dict, work_dir, max_tries: int = 3,
                               red_recall_fn=None, design=None) -> dict:
    """对 case 的 golden 生成一条**可修毒**. 可修性闸: buggy 编译+testbench-FAIL + golden-PASS.

    case 需含 golden_rtl/tb_sources/tb_output/top_module/sim_timeout(同 manifest).
    red_recall_fn(design): 红队记忆注入(避弱毒/升级深化); 升级的新毒覆盖旧毒例.
    返回 {ok, buggy_path, mutation_type, rationale, evidence, tries} 或 {ok:False, reason}.
    """
    wd = Path(work_dir)
    golden = case["golden_rtl"]
    design = design or case.get("design_name", "")
    red_ctx = red_recall_fn(design) if red_recall_fn else ""

    # 可修性前提: golden 自身过 testbench(否则该 design 对本 harness 无效).
    g = judge(case, golden, wd / "golden_check")
    if not g.ok:
        return {"ok": False, "reason": f"golden_invalid: {g.stage}/{g.err or g.mismatch}"}

    attempts = []
    for t in range(max_tries):
        m = mutate_once(golden, wd / f"try{t}", red_ctx=red_ctx)
        if "error" in m:
            attempts.append({"try": t, "stage": "mutate", "error": m["error"]})
            continue
        # 用 buggy 替换 candidate, golden 不变(case.golden_rtl=原 golden) → 判 buggy 是否功能错.
        v = judge(case, m["buggy_path"], wd / f"try{t}" / "judge")
        if v.stage == "cand_sim":
            attempts.append({
                "try": t, "stage": "cand_sim", "mutation_type": m.get("mutation_type"),
                "error": v.err or v.mismatch,
            })
            continue  # 编译错=语法毒, 违约束, 重试
        if v.ok:
            attempts.append({
                "try": t, "stage": "compare", "mutation_type": m.get("mutation_type"),
                "error": "candidate still matches golden",
            })
            continue  # 空变异(不改行为), 重试
        # v.stage=='compare' and not ok → 编译过 + 功能错 + golden 过(已验) = 合法可修毒
        return {
            "ok": True, "buggy_path": m["buggy_path"], "golden_rtl": golden,
            "top_module": case["top_module"], "tb_sources": case["tb_sources"],
            "tb_output": case["tb_output"], "deps": case.get("deps", []),
            "sim_timeout": case.get("sim_timeout", 10.0),
            "mutation_type": m["mutation_type"], "rationale": m["rationale"],
            "range": m["range"], "evidence": v.mismatch, "tries": t + 1,
            "repairable_cert": "golden testbench-PASS (revert=valid repair)",
        }
    return {
        "ok": False,
        "reason": f"no_valid_poison_in_{max_tries}_tries",
        "attempts": attempts,
    }


if __name__ == "__main__":
    import argparse
    from cirfix_adapter import parse_project

    ap = argparse.ArgumentParser()
    ap.add_argument("--toml", required=True, help="用其 golden 作投毒目标")
    ap.add_argument("--work", required=True)
    ap.add_argument("--n", type=int, default=1, help="生成几条可修毒")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = parse_project(args.toml)
    case = rows[0]  # 同 design 所有 bug 共享 golden/tb, 取第一行即可
    poisons = []
    for i in range(args.n):
        p = generate_repairable_poison(case, Path(args.work) / f"poison{i}")
        poisons.append(p)
        if p.get("ok"):
            print(f"[POISON {i}] OK type={p['mutation_type']} tries={p['tries']} "
                  f"evidence={p['evidence']}")
            print(f"           {p['rationale']}")
        else:
            print(f"[POISON {i}] FAIL: {p['reason']}")
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in poisons))
