"""回环合龙编排(路线 B, DI 解耦) — trajectory 后端 abstain → 前端等价修复 → reclose → loopback 蒸馏.

刀 B1: 只验**接线骨架**(控制流), 前端/reclose/distill 全用注入件(B1 注 mock, B2/B3 注真).
不复用 run_pipeline 那条绑死单cell run_eco_repair 的旧回环路径(操作者定); 这里用依赖注入把
trajectory↔前端↔reclose 解耦, 便于逐刀替换为真件.

阀门(§4.3): 仅 failure_class∈_ROLLBACK_CLASSES 触发回环; abstain(LLM判"回前端/架构")→
structural_bottleneck(新映射); routed_unclosed/exhausted→启发分类(tool_no_effect/sizing_plateau).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from microsurgeon_flow.failed_skill_builder import classify_sizing_failure
from microsurgeon_flow.rework_request import build_rework_request

# §4.3 回滚路由(对齐 orchestrator._ROLLBACK_CLASSES, 单一真相留 orchestrator, 这里引用副本注释).
_ROLLBACK_CLASSES = frozenset({
    "sizing_plateau", "tool_no_effect",                     # 路径A 耗尽型
    "structural_bottleneck", "overconstraint_infeasible",   # 路径B 根因型
})


@dataclass
class BackendOutcome:
    """trajectory 后端臂产出(回环判定 + rework_request 所需). evidence=None 表示无可用违例路径."""
    failure_class: Optional[str]
    evidence: object = None            # PathEvidence(cell_chain/startpoint/endpoint/slack)
    tried: list = field(default_factory=list)
    improved_ps: float = 0.0
    residual_gap_ps: float = 0.0
    exhausted_reason: str = ""
    case_id: str = ""
    violating_endpoints: list = field(default_factory=list)


@dataclass
class LoopbackResult:
    outcome: str                       # forward_only / loopback_frontend_failed / loopback_reopen_unclosed / loopback_closed
    failure_class: Optional[str] = None
    rework_request: Optional[dict] = None
    case_spec: Optional[dict] = None
    frontend_outcome: Optional[str] = None
    reclose_worst_slack: Optional[float] = None
    loopback_distilled: bool = False
    repaired_rtl_path: Optional[str] = None


def map_trajectory_to_failure_class(traj_result) -> Optional[str]:
    """TrajectoryResult → failure_class(回环阀门用). closed→None(不回环);
    abstain(LLM真判读)→structural_bottleneck; abstain(llm_error瞬时)→other(不回环);
    其余(routed_unclosed/exhausted/max_steps)→启发分类."""
    outcome = traj_result.outcome
    if outcome == "closed":
        return None
    if outcome == "abstain":
        reason = traj_result.abstain_reason or ""
        if reason.startswith("llm_error"):
            return "other"                                  # 瞬时失败非结构根因, 不回环
        return "structural_bottleneck"                      # LLM 判"需前端架构" → 结构根因
    wns_traj = [traj_result.initial_wns] + [s.wns_after for s in traj_result.steps]
    return classify_sizing_failure(
        wns_trajectory=wns_traj,
        rounds=len(traj_result.steps),
        distinct_strategies=len(set(traj_result.action_class_sequence)))


def _find_module_body_bounds(lines: list, module_name: str) -> tuple:
    """框选 module body(port list `);` 后 → endmodule 前), 去首尾空行. 1-based 闭区间."""
    start = None
    for i, line in enumerate(lines, 1):
        if re.match(rf"\s*module\s+{re.escape(module_name)}\b", line):
            start = i
            break
    if start is None:
        raise ValueError(f"module {module_name!r} not found")
    end = None
    for i in range(start + 1, len(lines) + 1):
        if re.match(r"\s*endmodule\b", lines[i - 1]):
            end = i
            break
    if end is None:
        raise ValueError(f"module {module_name!r} has no endmodule")
    header_end = None
    for i in range(start, end):
        if lines[i - 1].strip().startswith(");"):
            header_end = i
            break
    body_start = (header_end + 1) if header_end else (start + 1)
    while body_start < end and not lines[body_start - 1].strip():
        body_start += 1
    body_end = end - 1
    while body_end > body_start and not lines[body_end - 1].strip():
        body_end -= 1
    return body_start, body_end


def derive_structural_block_bounds(rtl_path, top_module: str, violating_endpoint=None) -> tuple:
    """感知层(P2): 从 RTL 自动框选待 retime 的块=违例寄存器所在模块体, 替代 N=1 手工 block_bounds.
    违例端点含 '.' 取其模块前缀(层级名), 否则用 top_module. 批跑前置(不能逐设计手指 block)."""
    from pathlib import Path as _P
    lines = _P(rtl_path).read_text(errors="ignore").splitlines()
    module = top_module
    if violating_endpoint and "." in str(violating_endpoint):
        module = str(violating_endpoint).split(".", 1)[0]
    return _find_module_body_bounds(lines, module)


def build_structural_case_spec(case: dict, rework: dict) -> dict:
    """结构修复版 case_spec(区别于 def-use 语义版): 塞 rework_request + repair_mode=structural_timing.
    前端据 rework.recommended_fix(retime/restructure, 等价保持)改 RTL; formal 序列等价是硬闸."""
    prob = rework["problem"]
    spec = {
        "buggy_rtl": case.get("rtl_path"),
        "top_module": case.get("top_module"),
        "deps": case.get("deps", []),
        "golden_sources": case.get("golden_sources", []),
        "repair_mode": "structural_timing",
        "rework_request": rework,
        "target_module": prob.get("module"),
        "endpoints": [prob.get("startpoint"), prob.get("endpoint")],
    }
    # block_bounds: 待重写块. 优先 case 手工指定; 否则感知层(P2)自动框选违例寄存器所在模块体.
    if "block_bounds" in case:
        spec["block_bounds"] = case["block_bounds"]
    elif "start_line" in case and "end_line" in case:
        spec["start_line"], spec["end_line"] = case["start_line"], case["end_line"]
    elif case.get("rtl_path") and case.get("top_module"):
        # best-effort: RTL 读不到/模块找不到 → 不补 block_bounds, 失败延后到 frontend(清晰报错), 不崩 spec.
        try:
            spec["block_bounds"] = list(derive_structural_block_bounds(
                case["rtl_path"], case["top_module"], prob.get("endpoint")))
        except (OSError, ValueError):
            pass
    # equiv_method 据推荐动作自动选: retime=时序变换(寄存器内容变)→seq_miter; restructure=组合→induct.
    action = rework.get("recommended_fix", {}).get("action")
    spec["equiv_method"] = "seq_miter" if action == "retime" else "induct"
    spec["seq_depth"] = case.get("seq_depth", 12)
    return spec


def run_loopback_closure(
    backend: BackendOutcome,
    case: dict,
    *,
    frontend_fn: Callable[[dict], dict],
    reclose_fn: Callable[[Optional[str]], tuple],
    distill_loopback_fn: Callable[..., bool],
    build_rework_fn: Callable[..., dict] = build_rework_request,
) -> LoopbackResult:
    """回环主控(DI). 阀门→返修请求→前端臂→reclose→loopback蒸馏. 每个真件由调用方注入.
      frontend_fn(case_spec) -> {"outcome": "ok"/..., "repaired_rtl_path": ..., "skill_name": ...}
      reclose_fn(repaired_rtl_path) -> (reclosed: bool, meta: {"worst_slack": float})
      distill_loopback_fn(backend=, frontend=, rework=) -> happened: bool
    """
    fc = backend.failure_class
    # 阀门: 非 rollback 类 → forward-only(fail-safe), 不回环
    if fc not in _ROLLBACK_CLASSES:
        return LoopbackResult(outcome="forward_only", failure_class=fc)

    # 返修请求(我的契约): 后端→前端, RTL级剥门级, 等价保持推荐
    rework = build_rework_fn(
        backend.evidence, tried=backend.tried, improved_ps=backend.improved_ps,
        residual_gap_ps=backend.residual_gap_ps, exhausted_reason=backend.exhausted_reason)
    case_spec = build_structural_case_spec(case, rework)

    # 前端臂(B1 mock): 等价保持结构修复
    fe = frontend_fn(case_spec)
    if fe.get("outcome") != "ok":
        return LoopbackResult(
            outcome="loopback_frontend_failed", failure_class=fc,
            rework_request=rework, case_spec=case_spec, frontend_outcome=fe.get("outcome"))

    # reclose(B1 mock): 重PnR finish worst_slack≥0?
    reclosed, meta = reclose_fn(fe.get("repaired_rtl_path"))
    if not reclosed:
        return LoopbackResult(
            outcome="loopback_reopen_unclosed", failure_class=fc,
            rework_request=rework, case_spec=case_spec, frontend_outcome="ok",
            reclose_worst_slack=meta.get("worst_slack"),
            repaired_rtl_path=fe.get("repaired_rtl_path"))

    # loopback 蒸馏(B1 mock): 跨域因果链入 loopback_lib
    happened = distill_loopback_fn(backend=backend, frontend=fe, rework=rework)
    return LoopbackResult(
        outcome="loopback_closed", failure_class=fc,
        rework_request=rework, case_spec=case_spec, frontend_outcome="ok",
        reclose_worst_slack=meta.get("worst_slack"),
        loopback_distilled=bool(happened),
        repaired_rtl_path=fe.get("repaired_rtl_path"))


# ── B3a: 真 adapter 工厂(注入 run_loopback_closure 的 DI 槽, 替 B1 mock) ──────────
def make_frontend_fn(work_dir, *, repair_fn=None, frontend_distill_fn=None,
                     default_skill_name="frontend_structural_timing"):
    """真前端臂: repair_one_case(structural_timing) → formal → {outcome, repaired_rtl_path, skill_name}.
    repair_fn 缺省 repair_one_case; frontend_distill_fn(可选)蒸前端 skill 供 loopback ref."""
    from pathlib import Path as _P
    if repair_fn is None:
        from microsurgeon_frontend.semantic.llm_micro_repair import repair_one_case as repair_fn

    def _fn(case_spec):
        rr = repair_fn(case_spec, _P(work_dir) / "frontend")
        if not getattr(rr, "success", False):
            return {"outcome": "equiv_failed", "repaired_rtl_path": getattr(rr, "repaired_rtl_path", None),
                    "proven": getattr(rr, "proven", None), "total": getattr(rr, "total", None)}
        skill_name = frontend_distill_fn(rr, case_spec) if frontend_distill_fn else default_skill_name
        return {"outcome": "ok", "repaired_rtl_path": rr.repaired_rtl_path, "skill_name": skill_name}
    return _fn


def make_reclose_fn(case, work_dir, *, adapter=None, parse_slack_fn=None):
    """真 reclose: ORFS 全流程重 PnR(修复后RTL) → finish worst_slack≥0? finish详布口径≈sign-off."""
    from pathlib import Path as _P
    if adapter is None:
        from microsurgeon_flow.orfs_reclose import ORFSRecloseAdapter
        adapter = ORFSRecloseAdapter(timeout_sec=int(case.get("reclose_timeout_sec", 3600)))
    if parse_slack_fn is None:
        from microsurgeon_flow.orfs_reclose import parse_finish_worst_slack as parse_slack_fn

    def _fn(repaired_rtl_path):
        stage = adapter.run_full_flow(
            design=case["design_name"], platform=case.get("platform", "nangate45"),
            variant=case.get("reclose_variant", "loopback_reclose"),
            verilog_files=[_P(repaired_rtl_path)], sdc=_P(case["sdc"]),
            work_dir=_P(work_dir) / "pnr_reclose",
            base_config=_P(case["orfs_base_config"]) if case.get("orfs_base_config") else None,
            config_overrides=case.get("orfs_config_overrides", {}))
        ws = parse_slack_fn(_P(stage.finish_rpt))
        return bool(ws is not None and ws >= 0), {"worst_slack": ws, "finish_rpt": stage.finish_rpt}
    return _fn


def make_distill_loopback_fn(distill_skill_fn=None):
    """真 loopback 蒸馏: distill_loopback_skill → loopback_lib(走 three_lib_gate, MEMORY_ROOT 决定真/隔离)."""
    if distill_skill_fn is None:
        from microsurgeon_flow.distill_loopback import distill_loopback_skill as distill_skill_fn

    def _fn(*, backend, frontend, rework):
        happened, _vet, _art = distill_skill_fn(
            case_id=backend.case_id,
            violating_endpoints=backend.violating_endpoints,
            frontend_skill_name=frontend.get("skill_name") or "",
            backend_failure_class=backend.failure_class or "unknown")
        return bool(happened)
    return _fn
