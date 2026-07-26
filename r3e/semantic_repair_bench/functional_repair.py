"""LLM 功能 bug 修复(最小, 判据驱动): buggy RTL + 仿真 mismatch 证据 → LLM 提 minimal
行级 patch → apply → oracle_gate 判 patched.

与现有蓝方链的区别: 现有 propose_patch_spec 是确定性规则(前端语法 F0/F4); 功能 bug 千变万化,
确定性规则不适用, 必须 LLM + 仿真证据. 这正是"确定性 baseline 失效子域"叙事的落点.

本期最小版: 单步无 memory(memory 消融留待接红队 curriculum 后). 验证管道接通 + 判据驱动.
"""
from __future__ import annotations

import hashlib
import json
import sys
import math
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).parent))
from microsurgeon_flow.backend_eco_oneshot import call_llm  # noqa: E402

from oracle_gate import judge  # noqa: E402
from skill_preflight import (  # noqa: E402
    build_preflight_decision,
    combine_recall_context,
    try_deterministic_template_patch,
)
# 复用蓝方系统真 primitive(纳入蓝方: 修复应用走 llm_micro_repair.apply_block_patch)
from microsurgeon_frontend.semantic.llm_micro_repair import (  # noqa: E402
    apply_block_patch as _blue_apply_block_patch,
)
from r3e.policy.runtime import PolicyRuntime, PolicyRuntimeViolation
from r3e.policy.schema import PolicyState

_PROMPT = """你是 RTL 功能 bug 修复专家。下面的 Verilog 模块**能编译通过**，但 testbench 仿真
输出与正确行为不符（功能 bug，非语法错）。请给出**最小**修复。

## Buggy RTL（带行号）
{rtl}

## 仿真证据（candidate 输出 vs 正确行为）
{evidence}
（这定位了 bug 的功能影响：上述信号在该周期的输出与正确电路不符。）
{memory}
## 任务：输出严格 JSON，最小行级修复（只改导致 bug 的行，禁止引入新 module/endmodule）：
{{"start_line": <起,1-based>, "end_line": <止,闭区间>, "new_code": "<替换这些行的正确代码，不带行号>", "rationale": "<≤2句：bug是什么、为何这样修>"}}
原则：改动尽量小；new_code 必须是完整可直接替换的整行；保持功能等价于正确电路。
"""

_MAX_BLOCK = 40


def _numbered(text: str) -> str:
    return "\n".join(f"{i}: {ln}" for i, ln in enumerate(text.splitlines(), 1))


def _safe(text: str):
    if "\x00" in text:
        raise ValueError("patch contains NUL")
    if "\nmodule " in "\n" + text or "\nendmodule" in "\n" + text:
        raise ValueError("patch introduces module/endmodule, rejected")


def _hybrid_evidence(pre) -> str:
    """Structured diagnosis plus the raw repeated mismatch trail for generation."""
    raw = (pre.detail or {}).get("raw_recurrence") or pre.mismatch
    if pre.structured and raw:
        return f"{pre.structured}\n\n[关键 raw recurrence]\n{raw}"
    return pre.structured or raw


def propose(
    buggy_rtl,
    evidence: str,
    memory_context: str = "",
    prompt_lens: str = "",
) -> dict:
    rtl = Path(buggy_rtl).read_text(errors="ignore")
    prompt = _PROMPT.format(rtl=_numbered(rtl), evidence=evidence,
                            memory=(
                                (f"\n## Frozen prompt lens\n{prompt_lens}\n" if prompt_lens else "")
                                + memory_context
                            ))
    result = call_llm(prompt)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    response_hash = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()

    # Some providers occasionally wrap an otherwise valid repair object in a
    # JSON list.  Hash the raw response once, then propagate that provenance to
    # every selectable object.  Previously only a top-level dict was stamped,
    # so list-wrapped candidates were evaluated but emitted with empty hashes.
    def stamp(item: dict) -> dict:
        item = dict(item)
        item["_formal_prompt_hash"] = prompt_hash
        item["_formal_response_hash"] = response_hash
        item["_formal_prompt_chars"] = len(prompt)
        item["_formal_response_chars"] = len(
            json.dumps(result, ensure_ascii=False, default=str)
        )
        return item

    if isinstance(result, dict):
        return stamp(result)
    if isinstance(result, list):
        stamped = [stamp(item) if isinstance(item, dict) else item for item in result]
        if any(isinstance(item, dict) for item in stamped):
            return stamped
        return {
            "llm_call_error": "llm returned list with no dict element",
            "_formal_prompt_hash": prompt_hash,
            "_formal_response_hash": response_hash,
        }
    return {
        "llm_call_error": f"unexpected response type: {type(result).__name__}",
        "_formal_prompt_hash": prompt_hash,
        "_formal_response_hash": response_hash,
    }


def apply_block(buggy_rtl, start: int, end: int, new_code: str, out_path) -> Path:
    # functional_repair 域安全检查(蓝方 apply_block_patch 无 module 注入 / 块过大守卫)
    lines = Path(buggy_rtl).read_text(errors="ignore").splitlines()
    if start < 1 or end > len(lines) or end < start:
        raise ValueError(f"range out of bounds: {start}-{end} (file {len(lines)} lines)")
    if end - start + 1 > _MAX_BLOCK:
        raise ValueError(f"block too large: {end - start + 1} lines")
    _safe(new_code)
    # 应用走蓝方真 primitive(line-delta 校验 + strip echoed line numbers + diff 审计)
    return _blue_apply_block_patch(
        Path(buggy_rtl),
        {"start_line": start, "end_line": end, "new_block": new_code},
        Path(out_path).parent,
    )


def repair_one(case: dict, work_dir, recall_fn=None, evidence_k: int = 1,
               n_candidates: int = 1, structured_evidence: bool = False,
               preflight_registry=None,
               enable_template_preflight: bool = False,
               policy: PolicyState | dict | None = None,
               policy_runtime: PolicyRuntime | None = None,
               formal_mode: bool = False) -> dict:
    """单 case 功能修复端到端. 返回结果 dict(judge 驱动).

    recall_fn(case) -> memory_context 字符串(注入 prompt). None=no-memory(L1).
    evidence_k: 给 LLM 几个 mismatch 作证据(1=baseline, >1=丰富证据攻 off-by-one).
    n_candidates: best-of-N 多候选采样(1=单候选; >1 多次 propose, 任一 judge 通过即修复).
    structured_evidence: True=hybrid evidence, 即 bus 重组+临界对照+偏移模式的结构化诊断
        再附关键 raw recurrence; 不再用结构化摘要替代 raw per-bit mismatch.
    preflight_registry: 非空时, 先按 promoted skill registry 路由 action_policy, 再收集证据/调用 LLM.
        这用于把 k6n3 默认降到 k1n3/k1n1, 减少 raw evidence prompt 体积.
    enable_template_preflight: 预留高置信 deterministic/template patch 尝试位. 当前无模板命中,
        只产 telemetry; 未来模板命中后先 apply+judge, 失败再进 LLM.
    """
    wd = Path(work_dir)
    rec = {"design": case["design_name"], "top": case["top_module"]}
    started = time.monotonic()
    runtime = policy_runtime
    if runtime is not None:
        policy = runtime.policy
    if isinstance(policy, dict):
        policy = PolicyState.from_dict(policy)
    if formal_mode:
        if policy is None:
            raise PolicyRuntimeViolation("formal repair requires a validated PolicyState")
        if recall_fn is not None or preflight_registry is not None:
            raise PolicyRuntimeViolation(
                "formal repair forbids recall_fn and manual skill preflight registry"
            )
        if enable_template_preflight:
            raise PolicyRuntimeViolation(
                "formal repair forbids legacy template preflight"
            )
        configuration = policy.configuration
        unsupported = []
        if configuration["candidate_selection"] != "first_verified":
            unsupported.append("candidate_selection")
        if configuration["model_route_id"] != "single_default":
            unsupported.append("model_route_id")
        if int(configuration["blue_population"]) != 1:
            unsupported.append("blue_population")
        if configuration["verifier_order"] != ["simulation"]:
            unsupported.append("verifier_order")
        if configuration["patch_scope"] != "local_block":
            unsupported.append("patch_scope")
        if unsupported:
            raise PolicyRuntimeViolation(
                f"formal repair runtime does not implement frozen fields: {unsupported}"
            )
        evidence_k = int(configuration["evidence_k"])
        n_candidates = int(configuration["n_candidates"])
        structured_evidence = configuration["evidence_mode"] == "hybrid"
    prompt_lens = runtime.prompt_template if runtime is not None else ""
    if formal_mode and not prompt_lens:
        lens_path = (
            Path(__file__).resolve().parents[2]
            / "configs"
            / "base_policy"
            / "prompt_templates"
            / f"{policy.configuration['prompt_lens_id']}.txt"
        )
        if not lens_path.is_file():
            raise PolicyRuntimeViolation("frozen prompt lens is missing")
        prompt_lens = lens_path.read_text(encoding="utf-8").strip()
    if policy is not None:
        rec.update({
            "policy_id": policy.policy_id,
            "policy_hash": policy.policy_hash,
            "configuration_hash": policy.configuration_hash,
            "policy_schema_version": policy.schema_version,
            "budget": dict(policy.budgets),
            "formal_mode": bool(formal_mode),
        })
    else:
        rec.update({
            "policy_id": "legacy",
            "policy_hash": "",
            "configuration_hash": "",
            "formal_mode": False,
        })

    if formal_mode:
        # Formal execution is driven only by PolicyState.  Do not even invoke
        # the legacy preflight compatibility layer.
        preflight = {
            "schema_version": "r3e-formal-policy-preflight-v2",
            "route": "policy_state_only",
            "skill_id": None,
            "has_strategy": False,
            "effective_policy": {
                "evidence_k": evidence_k,
                "n_candidates": n_candidates,
                "patch_scope": policy.configuration["patch_scope"],
            },
        }
    else:
        preflight = build_preflight_decision(
            case,
            preflight_registry,
            requested_evidence_k=evidence_k,
            requested_n_candidates=n_candidates,
            enable_template_preflight=enable_template_preflight,
        )
    effective_policy = preflight.get("effective_policy", {})
    evidence_k = int(effective_policy.get("evidence_k", evidence_k))
    n_candidates = int(effective_policy.get("n_candidates", n_candidates))
    rec["preflight"] = {
        k: v for k, v in preflight.items()
        if k != "strategy_context"
    }
    rec["effective_evidence_k"] = evidence_k
    rec["effective_n_candidates"] = n_candidates

    pre = judge(case, case["buggy_rtl"], wd / "pre", evidence_k=evidence_k)
    rec["buggy_ok"] = pre.ok
    rec["buggy_stage"] = pre.stage
    rec["buggy_evidence"] = pre.mismatch or pre.err
    rec["oracle_provenance"] = {
        key: (pre.detail or {}).get(key)
        for key in ("command_hash", "toolchain_fingerprint_hash", "tools")
        if (pre.detail or {}).get(key) is not None
    }
    if pre.ok:
        rec["note"] = "buggy_already_passes(数据集异常)"
        return rec
    if pre.stage == "golden_sim":
        rec["note"] = f"golden 跑不了, case 无效: {pre.err}"
        return rec

    template_attempt = try_deterministic_template_patch(case, preflight, pre)
    rec["preflight"]["template_attempt"] = {
        k: v for k, v in template_attempt.items()
        if k != "patch"
    }
    if template_attempt.get("hit") and template_attempt.get("patch"):
        tr = {"source": "deterministic_template",
              "template_id": template_attempt.get("template_id")}
        patch = template_attempt["patch"]
        try:
            patched = apply_block(case["buggy_rtl"], int(patch["start_line"]),
                                  int(patch["end_line"]), patch["new_code"],
                                  wd / "patched_template.v")
            post = judge(case, patched, wd / "post_template")
            tr["patched_ok"] = post.ok
            tr["patched_evidence"] = post.mismatch or post.err
        except Exception as e:  # noqa: BLE001
            tr["apply_error"] = str(e)
        rec["template_candidate"] = tr
        if tr.get("patched_ok"):
            rec["repair_source"] = "deterministic_template"
            rec["repaired"] = True
            rec["n_candidates_tried"] = 0
            return rec

    memory_context = (
        "" if formal_mode else combine_recall_context(case, preflight, recall_fn)
    )
    rec["used_memory"] = bool(memory_context)
    rec["used_strategy"] = bool(preflight.get("has_strategy"))

    # generation 证据: hybrid(结构化诊断 + raw recurrence) 或 raw per-bit mismatch. err 兜底.
    ev_str = ((_hybrid_evidence(pre) if structured_evidence and pre.structured else pre.mismatch)
              or pre.err)
    rec["evidence_mode"] = "hybrid" if (structured_evidence and pre.structured) else "raw"

    # best-of-N 多候选: 逐个 propose→apply→judge, 任一通过即修复(利用 LLM 非确定性多样性).
    cand_recs = []
    max_llm_calls = (
        int(policy.budgets["max_llm_calls_per_case"]) if policy is not None
        else max(1, n_candidates)
    )
    max_wall_seconds = (
        int(policy.budgets["max_wall_seconds_per_case"]) if policy is not None
        else float("inf")
    )
    max_tokens = (
        int(policy.budgets["max_tokens_per_case"]) if policy is not None
        else 2 ** 63 - 1
    )
    estimated_tokens = 0
    repair_loop = policy.configuration["repair_loop"] if policy is not None else "one-shot"
    dynamic_evidence = ev_str
    for ci in range(max(1, n_candidates)):
        if len(cand_recs) >= max_llm_calls:
            rec["budget_exhausted"] = "max_llm_calls_per_case"
            break
        if time.monotonic() - started > max_wall_seconds:
            rec["budget_exhausted"] = "max_wall_seconds_per_case"
            break
        cr = {"cand": ci}
        prop = propose(
            case["buggy_rtl"],
            dynamic_evidence,
            memory_context,
            prompt_lens=prompt_lens,
        )
        if isinstance(prop, list):
            prop = next((x for x in prop if isinstance(x, dict)), None) or {
                "llm_call_error": "llm returned list with no dict element"}
        if not isinstance(prop, dict) or "llm_call_error" in prop:
            if isinstance(prop, dict):
                cr["prompt_hash"] = prop.get("_formal_prompt_hash")
                cr["response_hash"] = prop.get("_formal_response_hash")
            cr["error"] = (prop.get("llm_call_error", "bad_format")
                           if isinstance(prop, dict) else "bad_format")
            cand_recs.append(cr)
            continue
        estimated_tokens += math.ceil(
            (int(prop.get("_formal_prompt_chars") or 0)
             + int(prop.get("_formal_response_chars") or 0)) / 4
        )
        if estimated_tokens > max_tokens:
            cr["error"] = "max_tokens_per_case exceeded"
            cand_recs.append(cr)
            rec["budget_exhausted"] = "max_tokens_per_case"
            break
        cr["rationale"] = prop.get("rationale", "")
        cr["patch_range"] = [prop.get("start_line"), prop.get("end_line")]
        cr["prompt_hash"] = prop.get("_formal_prompt_hash")
        cr["response_hash"] = prop.get("_formal_response_hash")
        cr["patch_hash"] = hashlib.sha256(str(prop.get("new_code", "")).encode()).hexdigest()
        try:
            patched = apply_block(case["buggy_rtl"], int(prop["start_line"]),
                                  int(prop["end_line"]), prop["new_code"],
                                  wd / f"patched_c{ci}.v")
        except Exception as e:  # noqa: BLE001
            cr["apply_error"] = str(e)
            cand_recs.append(cr)
            continue
        post = judge(case, patched, wd / f"post_c{ci}")
        cr["patched_ok"] = post.ok
        cr["oracle_stage"] = post.stage
        cr["compile_ok"] = post.stage == "compare"
        cr["simulation_ok"] = post.stage == "compare"
        cr["patched_evidence"] = post.mismatch or post.err
        cr["oracle_command_hash"] = (post.detail or {}).get("command_hash")
        cr["toolchain_fingerprint_hash"] = (
            post.detail or {}
        ).get("toolchain_fingerprint_hash")
        cand_recs.append(cr)
        if post.ok:  # 命中即停
            break
        if repair_loop == "critique-revise":
            dynamic_evidence = (
                f"{ev_str}\n\n[Prior candidate failed verification]\n"
                f"{post.mismatch or post.err}"
            )

    rec["candidates"] = cand_recs
    rec["n_candidates_tried"] = len(cand_recs)
    if not cand_recs:
        rec["repaired"] = False
        rec["repair_source"] = None
        rec["error"] = str(rec.get("budget_exhausted") or "no_candidate_generated")
        rec["budget_usage"] = {
            "llm_calls": 0,
            "estimated_tokens": estimated_tokens,
            "wall_seconds": round(time.monotonic() - started, 6),
        }
        return rec
    win = next((c for c in cand_recs if c.get("patched_ok")), None)
    chosen = win or cand_recs[-1]
    rec["llm_rationale"] = chosen.get("rationale", "")
    rec["patch_range"] = chosen.get("patch_range")
    rec["error"] = chosen.get("error", "")
    rec["apply_error"] = chosen.get("apply_error", "")
    rec["patched_evidence"] = chosen.get("patched_evidence", "")
    rec["repaired"] = win is not None
    rec["repair_source"] = "llm" if win is not None else None
    rec["budget_usage"] = {
        "llm_calls": len(cand_recs),
        "estimated_tokens": estimated_tokens,
        "wall_seconds": round(time.monotonic() - started, 6),
    }
    return rec


if __name__ == "__main__":
    import argparse
    import json
    from cirfix_adapter import parse_project

    ap = argparse.ArgumentParser()
    ap.add_argument("--toml", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--bug", action="append", default=None,
                    help="只跑指定 bug 名(可重复); 缺省全 bug")
    ap.add_argument("--preflight-registry", default=None,
                    help="启用 skill preflight, 指向 .iso_semrepair/skills.json")
    ap.add_argument("--template-preflight", action="store_true",
                    help="启用 deterministic/template patch 预尝试接口(当前仅 telemetry)")
    args = ap.parse_args()

    rows = parse_project(args.toml)
    n_repaired = 0
    n_func = 0
    for r in rows:
        bug = r["design_name"].split("__", 1)[1]
        if args.bug and bug not in args.bug:
            continue
        res = repair_one(
            r,
            Path(args.work) / r["design_name"],
            preflight_registry=args.preflight_registry,
            enable_template_preflight=args.template_preflight,
        )
        if res.get("buggy_stage") == "compare":
            n_func += 1
        if res.get("repaired"):
            n_repaired += 1
        print(json.dumps(res, ensure_ascii=False))
    print(f"\n=== 功能型 bug={n_func} | 修复(judge PASS)={n_repaired} ===")
