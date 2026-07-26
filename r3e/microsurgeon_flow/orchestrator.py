"""§4.6 forward-only 薄编排层 + §4.2-4.5 回环骨架(Step5a 真 PnR re-close).

forward-only 边界(§4.6):
- triage RuntimeError(环境失败) -> 记 triage_env_fail 直接返回, 不恢复.
- 不对称(实证): 后端执行体内嵌蒸馏(回读 _distill_happened, 不重复调);
  前端执行体(repair_one_case)只产 RepairResult, 蒸馏由本层调 distill_frontend_skill.
- (iii) 范围: 前端直入仍显式携带 case_spec; 后端回环只从违例 def-use 构造.
- 本层是 triage() 与 distill_frontend_skill() 的首个主线调用点.

Step5a 回环:
- 后端臂 return 后检查 rollback_requested → 路由回前端臂再入 → 真跑 PnR re-close
- closed 判据只读 re-close STA worst slack, 不读 adapter returncode
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from microsurgeon_flow.backend_eco_oneshot import run_eco_repair
from microsurgeon_flow.distill_frontend import distill_frontend_skill
from microsurgeon_flow.distill_loopback import distill_loopback_skill
from microsurgeon_flow.orfs_reclose import (
    DEFAULT_FLOW_ROOT,
    ORFSRecloseAdapter,
    parse_finish_worst_slack,
)
from microsurgeon_flow.triage import triage
from microsurgeon_frontend.semantic.llm_micro_repair import repair_one_case
from microsurgeon_frontend.semantic.propose_patch_spec import whole_word

def _frontend_skill_naming(rr, case: dict) -> tuple[str, str]:
    strategy = (
        "llm_block_rewrite"
        if getattr(rr, "repair_mode", "identifier") == "block"
        else "llm_single_site_fix"
    )
    error_type = (
        "timing_structural"
        if case.get("_loopback_origin")
        else "undeclared_identifier"
    )
    return error_type, strategy

# ── §4.3 回滚路由: 仅四类触发回环, other/None/未知 → forward-only ──────────
_ROLLBACK_CLASSES = frozenset({
    "sizing_plateau", "tool_no_effect",            # 路径A 耗尽型
    "structural_bottleneck", "overconstraint_infeasible",  # 路径B 根因型
})

@dataclass
class OrchestrationResult:
    domain: str                    # "frontend" | "backend" | "?"
    repair_outcome: str            # ok/equiv_failed/fail/<eco status>/triage_env_fail/frontend_propose_fail
    distilled: bool
    skill_name: Optional[str]
    artifacts_root: str
    case_id: Optional[str] = None
    violating_endpoints: list = field(default_factory=list)
    loopback_distilled: bool = False
    failure_class: Optional[str] = None  # 刀一: §4.3 四类, None=无失败/不可判
    repaired_rtl_path: Optional[str] = None
    reclose_worst_slack: Optional[float] = None
    reclose_finish_rpt: Optional[str] = None


def _require(case: dict, keys: list, domain: str) -> None:
    missing = [k for k in keys if k not in case]
    if missing:
        raise ValueError(f"{domain} arm missing case fields: {missing}")


def _is_def_line(line: str, signal: str) -> bool:
    if re.search(rf"\b(?:wire|reg|logic)\b.*{re.escape(signal)}", line):
        return True
    return re.search(rf"^\s*assign\s+{re.escape(signal)}\b", line) is not None


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _derive_def_use_context_ranges(
    rtl_path: Path,
    signal: str,
) -> tuple[list[tuple[int, int]], int]:
    """
    Build broad context from backend-reported signal def-use sites.

    The guard is intentional: ranges come from all RTL occurrences of the
    violation signal, expanded into neighborhoods. They are not allowed to be
    pre-cut to just the answer line(s).
    """
    lines = Path(rtl_path).read_text(errors="ignore").splitlines()
    pattern = whole_word(signal)
    def_lines: list[int] = []
    use_lines: list[int] = []
    for idx, line in enumerate(lines, start=1):
        if not pattern.search(line):
            continue
        if _is_def_line(line, signal):
            def_lines.append(idx)
        else:
            use_lines.append(idx)

    if not def_lines and not use_lines:
        raise ValueError(f"violating signal {signal!r} not found in RTL {rtl_path}")

    ranges: list[tuple[int, int]] = []
    if def_lines:
        ranges.append((max(1, min(def_lines) - 9), min(len(lines), max(def_lines) + 4)))
    for line_no in use_lines:
        ranges.append((max(1, line_no - 13), min(len(lines), line_no + 7)))

    target_line = use_lines[0] if use_lines else def_lines[-1]
    return _merge_ranges(ranges), target_line


def _find_module_bounds(lines: list[str], module_name: str) -> tuple[int, int]:
    start = None
    for idx, line in enumerate(lines, start=1):
        if re.match(rf"\s*module\s+{re.escape(module_name)}\b", line):
            start = idx
            break
    if start is None:
        raise ValueError(f"module {module_name!r} not found")
    for idx in range(start + 1, len(lines) + 1):
        if re.match(r"\s*endmodule\b", lines[idx - 1]):
            return start, idx
    raise ValueError(f"module {module_name!r} has no endmodule")


def _derive_block_bounds_from_violation_signal(
    rtl_path: Path,
    signal: str,
) -> tuple[int, int]:
    """
    Derive a rewrite block from a backend-reported RTL signal.

    For hierarchical temp signals such as sub$out, the instance name ("sub")
    anchors the block: find that instance's module type, then frame the module's
    internal implementation body. The LLM may rewrite only that body.
    """
    lines = Path(rtl_path).read_text(errors="ignore").splitlines()
    if "$" not in signal:
        ranges, _target = _derive_def_use_context_ranges(rtl_path, signal)
        if len(ranges) != 1:
            raise ValueError(
                f"cannot derive a single block for signal {signal!r}: {ranges!r}"
            )
        return ranges[0]

    instance_name = signal.split("$", 1)[0]
    instance_re = re.compile(
        rf"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+{re.escape(instance_name)}\b"
    )
    module_type = None
    for line in lines:
        match = instance_re.match(line)
        if match:
            module_type = match.group(1)
            break
    if module_type is None:
        raise ValueError(f"instance {instance_name!r} for signal {signal!r} not found")

    module_start, module_end = _find_module_bounds(lines, module_type)
    header_end = None
    for idx in range(module_start, module_end + 1):
        if lines[idx - 1].strip() == ");":
            header_end = idx
            break
    if header_end is None:
        raise ValueError(f"module {module_type!r} header end not found")

    start = header_end + 1
    while start < module_end and not lines[start - 1].strip():
        start += 1
    end = module_end - 1
    while end >= start and not lines[end - 1].strip():
        end -= 1
    if start > end:
        raise ValueError(f"module {module_type!r} has no implementation block")
    return start, end


def build_case_spec_from_backend_violation(case: dict) -> dict:
    """
    Construct frontend repair case_spec strictly from backend violation evidence.

    golden_sources remain path-only and are only consumed later by verify_equiv.
    """
    _require(
        case,
        [
            "rtl_path",
            "route_report",
            "violation_endpoint",
            "violating_signal",
            "golden_sources",
            "deps",
            "top_module",
        ],
        "backend→frontend case_spec",
    )
    rtl_path = Path(case["rtl_path"])
    violating_signal = case["violating_signal"]
    context_ranges, target_line = _derive_def_use_context_ranges(
        rtl_path, violating_signal
    )
    spec = {
        "buggy_rtl": str(rtl_path),
        "error_log": str(Path(case["route_report"])),
        "target_line": target_line,
        "old_identifier": violating_signal,
        "context_ranges": context_ranges,
        "golden_sources": [str(Path(p)) for p in case["golden_sources"]],
        "deps": [str(Path(p)) for p in case["deps"]],
        "top_module": case["top_module"],
        "expected_file": case.get("expected_file", rtl_path.name),
        "backend_violation": {
            "endpoint": case["violation_endpoint"],
            "signal": violating_signal,
            "route_report": str(Path(case["route_report"])),
        },
    }
    if case.get("repair_mode") == "block":
        start_line, end_line = _derive_block_bounds_from_violation_signal(
            rtl_path, violating_signal
        )
        spec.update({
            "repair_mode": "block",
            "start_line": start_line,
            "end_line": end_line,
            "target_line": start_line,
            "block_bounds": [start_line, end_line],
            "context_ranges": [(start_line, end_line)],
        })
    return spec


_BACKEND_VIOLATION_CASE_KEYS = frozenset({
    "route_report",
    "violation_endpoint",
    "violating_signal",
    "golden_sources",
    "deps",
    "top_module",
})


def _case_for_loopback_frontend(case: dict) -> dict:
    if _BACKEND_VIOLATION_CASE_KEYS.issubset(case):
        fe_case = dict(case)
        fe_case["case_spec"] = build_case_spec_from_backend_violation(case)
        fe_case["_loopback_origin"] = True
        fe_case.setdefault("rtl_rel_path", Path(case["rtl_path"]).name)
        return fe_case
    if "case_spec" in case:
        fe_case = dict(case)
        fe_case["_loopback_origin"] = True
        return fe_case
    fe_case = dict(case)
    fe_case["case_spec"] = build_case_spec_from_backend_violation(case)
    fe_case["_loopback_origin"] = True
    fe_case.setdefault("rtl_rel_path", Path(case["rtl_path"]).name)
    return fe_case


def _reclose_pnr(case: dict, fe_result: OrchestrationResult, work_dir: Path) -> tuple[bool, dict]:
    _require(case, ["sdc"], "PnR re-close")
    if not fe_result.repaired_rtl_path:
        raise ValueError("PnR re-close missing frontend repaired_rtl_path")

    design = case["design_name"]
    platform = case.get("platform", "nangate45")
    variant = case.get("reclose_variant", "loopback_reclose")
    adapter = ORFSRecloseAdapter(
        flow_root=Path(case.get("flow_root", DEFAULT_FLOW_ROOT)),
        timeout_sec=int(case.get("reclose_timeout_sec", 3600)),
    )
    stage = adapter.run_full_flow(
        design=design,
        platform=platform,
        variant=variant,
        verilog_files=[Path(fe_result.repaired_rtl_path)],
        sdc=Path(case["sdc"]),
        work_dir=work_dir / "pnr_reclose",
        base_config=Path(case["orfs_base_config"]) if case.get("orfs_base_config") else None,
        config_overrides=case.get("orfs_config_overrides", {}),
    )
    worst_slack = parse_finish_worst_slack(Path(stage.finish_rpt))
    return bool(worst_slack is not None and worst_slack >= 0), {
        "stage": stage,
        "worst_slack": worst_slack,
    }


def run_pipeline(case: dict, work_dir: Path) -> OrchestrationResult:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    _require(case, ["design_name", "rtl_path"], "pipeline")

    try:
        domain = triage(Path(case["rtl_path"]), case["design_name"], work_dir)
    except RuntimeError:
        return OrchestrationResult(
            domain="?", repair_outcome="triage_env_fail", distilled=False,
            skill_name=None, artifacts_root=str(work_dir), case_id=None,
        )

    if domain == "frontend":
        return _run_frontend_arm(case, work_dir)
    if domain == "backend":
        be_result = _run_backend_arm(case, work_dir)
        # ── 刀一: §4.3 failure_class 路由 ──────────────────────────────
        # not in _ROLLBACK_CLASSES = fail-safe: None/other/未知 → forward-only
        if be_result.failure_class not in _ROLLBACK_CLASSES:
            return be_result

        # 分支2: 回退到前端臂再入(新 work_dir 避旧产物)
        fe_work_dir = work_dir.parent / f"{work_dir.name}_loopback_fe"
        fe_work_dir.mkdir(parents=True, exist_ok=True)
        loopback_case = _case_for_loopback_frontend(case)
        fe_result = _run_frontend_arm(loopback_case, fe_work_dir)

        # 分支2: 前端修复未过 formal → 不进 loopback_lib
        if fe_result.repair_outcome != "ok":
            be_result.repair_outcome = "loopback_frontend_failed"
            return be_result

        # 分支3: 重跑 PnR 验 closed — 独立读 STA worst slack, 不读 returncode
        reclosed, reclose_meta = _reclose_pnr(loopback_case, fe_result, fe_work_dir)
        if not reclosed:
            be_result.repair_outcome = "loopback_reopen_unclosed"
            be_result.reclose_worst_slack = reclose_meta["worst_slack"]
            be_result.reclose_finish_rpt = reclose_meta["stage"].finish_rpt
            return be_result

        # 分支4: 回环成功 → 蒸 loopback_lib 首条(跨域因果链)
        happened, _vet, _art = distill_loopback_skill(
            case_id=be_result.case_id or "",
            violating_endpoints=be_result.violating_endpoints,
            frontend_skill_name=fe_result.skill_name or "",
            backend_failure_class=be_result.failure_class or "unknown",
        )
        return OrchestrationResult(
            domain="loopback",
            repair_outcome="loopback_closed",
            distilled=fe_result.distilled,
            skill_name=fe_result.skill_name,
            artifacts_root=str(fe_work_dir),
            case_id=be_result.case_id,
            violating_endpoints=be_result.violating_endpoints,
            loopback_distilled=bool(happened),
            failure_class=be_result.failure_class,
            repaired_rtl_path=fe_result.repaired_rtl_path,
            reclose_worst_slack=reclose_meta["worst_slack"],
            reclose_finish_rpt=reclose_meta["stage"].finish_rpt,
        )
    raise ValueError(f"unknown triage domain: {domain!r}")


def _run_frontend_arm(case: dict, work_dir: Path) -> OrchestrationResult:
    # (iii): case 显式携带 case_spec + rtl_rel_path, 本层不提取
    _require(case, ["case_spec", "rtl_rel_path"], "frontend")
    design = case["design_name"]

    # repair_one_case: LLM propose 失败抛 RuntimeError; equiv 没过返回 success=False
    try:
        rr = repair_one_case(case["case_spec"], work_dir)
    except RuntimeError:
        return OrchestrationResult(
            domain="frontend", repair_outcome="frontend_propose_fail", distilled=False,
            skill_name=None, artifacts_root=str(work_dir), case_id=None,
        )

    # case_id 在拿到 rr.target_line 后才能按真库风格构造
    case_id = f"CASE-FRONTEND-{design.upper()}-L{rr.target_line}"

    # 蒸馏 gate: 必须 formal 真过(success 内部已含 proven==total>0), 且 total 有值.
    # 绝不把未过 formal 的修复蒸进 frontend_lib(真库 2 条 bit 冻结).
    if not (rr.success and rr.total and rr.total > 0):
        return OrchestrationResult(
            domain="frontend", repair_outcome="equiv_failed", distilled=False,
            skill_name=None, artifacts_root=str(work_dir), case_id=case_id,
            repaired_rtl_path=rr.repaired_rtl_path,
        )

    error_signature = (
        f"Unable to resolve identifier {rr.old_identifier} "
        f"at {rr.patch['file']}:{rr.target_line}"
    )
    context_pattern = f"design={design},file={rr.patch['file']},stage=yosys_synthesis"
    error_type, strategy = _frontend_skill_naming(rr, case)
    skill_name = f"frontend_{error_type}_{strategy}_{design}_L{rr.target_line}"

    happened, _vet, _artifact = distill_frontend_skill(
        rr,
        case_id=case_id,
        error_signature=error_signature,
        context_pattern=context_pattern,
        skill_name=skill_name,
        rtl_rel_path=case["rtl_rel_path"],
    )
    return OrchestrationResult(
        domain="frontend", repair_outcome="ok", distilled=bool(happened),
        skill_name=skill_name if happened else None,
        artifacts_root=str(work_dir), case_id=case_id,
        repaired_rtl_path=rr.repaired_rtl_path,
    )


def _run_backend_arm(case: dict, work_dir: Path) -> OrchestrationResult:
    _require(case, ["odb", "sdc", "period"], "backend")   # E=eco 输入契约
    design = case["design_name"]
    case_id = f"{design}_{case.get('poison_id', 'na')}_{time.strftime('%Y%m%dT%H%M%S')}"
    eco_dir = work_dir / "eco"

    result = run_eco_repair(
        case["odb"], case["sdc"],
        baseline_wns=case.get("baseline_wns", -0.06),
        max_iter=case.get("max_iter", 3),
        artifacts_dir=eco_dir,
        design=design,
        platform=case.get("platform", "nangate45"),
        variant=case.get("variant", "base"),
        period=case["period"],
        case_id=case_id,
        skill_lib=case.get("skill_lib") or case.get("backend_skill_lib"),
        template_lib=case.get("template_lib") or case.get("backend_template_lib"),
        pattern_template_lib=(
            case.get("pattern_template_lib") or case.get("backend_pattern_template_lib")
        ),
        enable_skill_preflight=case.get("enable_skill_preflight", True),
        recall_k=case.get("recall_k", 3),
        recall_candidate_k=case.get("recall_candidate_k", 12),
        preflight_max_actions=case.get("preflight_max_actions", 3),
        enable_template_preflight=case.get("enable_template_preflight", False),
    )
    # 后端蒸馏已内嵌(efca7c4), 此处只回读 commit-2 补的 telemetry, 严禁重复调蒸馏.
    return OrchestrationResult(
        domain="backend",
        repair_outcome=result.get("status", "unknown"),
        distilled=bool(result.get("_distill_happened", False)),
        skill_name=result.get("skill_name"),
        artifacts_root=str(eco_dir),
        case_id=case_id,
        violating_endpoints=result.get("violating_endpoints", []),
        failure_class=result.get("failure_class"),
    )
