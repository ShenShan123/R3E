"""方案 D 第一版: 路径级轨迹 agent 骨架(trajectory agent).

LLM 读路径级 STA 证据 -> 提 3 候选 -> 各候选真实评估(裁判) -> LLM 推理选择(非贪心)
-> commit -> 中间评估 -> 终止(closed/abstain/exhausted/max_steps) -> 蒸策略抽象层.

刀 2: 纯控制流骨架, propose/eval/select 三接口注入(mock 验逻辑, 刀 3 注真实现).
不改 backend_eco_oneshot(单 cell 对照基线), 不接 orchestrator 主线(独立 driver).
动作集第一版只 sizing(upsize/downsize) + abstain; 边界封闭不下沉布局/布线.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class PathEvidence:
    """路径级 STA 证据(刀1保序 cell 链 + 端点 + slack). 刀2 mock, 刀3 真 STA."""

    cell_chain: list[dict]
    startpoint: str
    endpoint: str
    slack: float


@dataclass
class EcoCandidate:
    target_inst: str
    new_master: str
    action_class: str
    reason: str


@dataclass
class ProposeResult:
    """LLM 提候选结果: 3 候选, 或 abstain."""

    candidates: list[EcoCandidate] = field(default_factory=list)
    is_abstain: bool = False
    abstain_reason: Optional[str] = None


@dataclass
class EvalResult:
    new_wns: float
    cand_odb_path: str


@dataclass
class TrajectoryStep:
    step: int
    candidates: list[EcoCandidate]
    eval_wns: list[float]
    selected_idx: int
    select_reason: str
    wns_after: float


@dataclass
class TrajectoryState:
    """喂给 propose/select 的当前状态(轨迹历史 + 当前 WNS)."""

    steps_so_far: list[TrajectoryStep]
    cur_wns: float


@dataclass
class TrajectoryResult:
    outcome: str
    steps: list[TrajectoryStep]
    final_wns: float
    initial_wns: float
    abstain_reason: Optional[str] = None
    action_class_sequence: list[str] = field(default_factory=list)
    distilled: bool = False
    skill_name: Optional[str] = None
    final_odb: Optional[str] = None   # 终止时的 odb 路径(sign-off 口径复核/GDS 旁证用)


ProposeFn = Callable[[PathEvidence, TrajectoryState], ProposeResult]
EvalFn = Callable[[EcoCandidate, str], EvalResult]
SelectFn = Callable[[list[tuple[EcoCandidate, EvalResult]], TrajectoryState], int]
ReadEvidenceFn = Callable[[str], PathEvidence]
InitWnsFn = Callable[[str], float]
DistillFn = Callable[["TrajectoryResult"], "tuple[bool, Optional[str]]"]


def run_trajectory(
    initial_odb: str,
    period: float,
    design: str,
    propose_fn: ProposeFn,
    eval_fn: EvalFn,
    select_fn: SelectFn,
    read_evidence_fn: ReadEvidenceFn,
    init_wns_fn: InitWnsFn,
    max_steps: int = 15,
    early_stop_k: int = 3,
    distill_fn: Optional[DistillFn] = None,
) -> TrajectoryResult:
    """路径乙闭环轨迹.

    每步: 读证据 -> 提 3 候选(或 abstain) -> 各候选真评估 -> LLM 选
    -> commit -> 中间评估.

    终止: WNS>=0 closed / LLM abstain / 连续 early_stop_k 步无改善 exhausted /
    max_steps 上限. 早停用 best-so-far, 非首尾.
    """
    del period, design  # 刀2 只验控制流; 刀3 真实实现会使用这些上下文.

    cur_odb = initial_odb
    initial_wns = init_wns_fn(cur_odb)
    cur_wns = initial_wns
    steps: list[TrajectoryStep] = []
    best_wns = initial_wns
    no_improve = 0

    for step in range(1, max_steps + 1):
        evidence = read_evidence_fn(cur_odb)
        state = TrajectoryState(steps_so_far=list(steps), cur_wns=cur_wns)

        proposal = propose_fn(evidence, state)
        if proposal.is_abstain:
            return _finalize(
                "abstain",
                steps,
                cur_wns,
                initial_wns,
                abstain_reason=proposal.abstain_reason,
                distill_fn=distill_fn,
                final_odb=cur_odb,
            )

        evaluated = [(candidate, eval_fn(candidate, cur_odb))
                     for candidate in proposal.candidates]

        selected_idx = select_fn(evaluated, state)
        selected_candidate, selected_eval = evaluated[selected_idx]

        cur_odb = selected_eval.cand_odb_path
        cur_wns = selected_eval.new_wns
        steps.append(TrajectoryStep(
            step=step,
            candidates=[candidate for candidate, _ in evaluated],
            eval_wns=[result.new_wns for _, result in evaluated],
            selected_idx=selected_idx,
            select_reason=selected_candidate.reason,
            wns_after=cur_wns,
        ))

        if cur_wns >= 0:
            return _finalize("closed", steps, cur_wns, initial_wns,
                             distill_fn=distill_fn, final_odb=cur_odb)

        if cur_wns > best_wns + 1e-6:
            best_wns = cur_wns
            no_improve = 0
        else:
            no_improve += 1
        if no_improve >= early_stop_k:
            return _finalize("exhausted", steps, cur_wns, initial_wns,
                             distill_fn=distill_fn, final_odb=cur_odb)

    return _finalize("max_steps", steps, cur_wns, initial_wns,
                     distill_fn=distill_fn, final_odb=cur_odb)


def _finalize(
    outcome: str,
    steps: list[TrajectoryStep],
    final_wns: float,
    initial_wns: float,
    abstain_reason: Optional[str] = None,
    distill_fn: Optional[DistillFn] = None,
    final_odb: Optional[str] = None,
) -> TrajectoryResult:
    """收口: 抽 action_class_sequence(策略抽象层), 构造结果, 可选真蒸馏.

    action_class_sequence 抽粗粒度 upsize/downsize, 非实例名/非 WNS 字面量.
    distill_fn=None(刀2 mock 默认) → distilled=False, 行为零变化.
    distill_fn 提供(driver 真跑) → 接 §4.7 失败技能蒸馏; gate1/异常打印不阻断.
    """
    sequence = [step.candidates[step.selected_idx].action_class for step in steps]
    result = TrajectoryResult(
        outcome=outcome,
        steps=steps,
        final_wns=final_wns,
        initial_wns=initial_wns,
        abstain_reason=abstain_reason,
        action_class_sequence=sequence,
        distilled=False,
        skill_name=None,
        final_odb=final_odb,
    )
    if distill_fn is not None:
        try:
            happened, skill_name = distill_fn(result)
            result.distilled, result.skill_name = happened, skill_name
        except SystemExit as exc:        # three_lib_gate gate1 abort: 打印不阻断
            print(f"[TRAJ_DISTILL] gate abort(继续 return): {exc}")
        except Exception as exc:
            print(f"[TRAJ_DISTILL] 蒸馏异常(继续 return): {exc}")
    return result


# ───────────────── 刀3 真实现(P1(b) 双向 legal + 五注入点 + 蒸馏) ─────────────────
_TRAJ_SECTION_HEADER = "finish report_checks -path_delay max reg to reg"  # 顶部补合成段头
_EVAL_FAIL_WNS = -1e6   # 评估失败 sentinel(非 max, 不污染正常比较)


def _scrub_wns_literals(text: str) -> str:
    """§4.7 红线: select_reasons 作证据但不蒸 WNS 字面量 → 数字 token 脱敏."""
    return re.sub(r"[-+]?\d+\.?\d*", "<num>", text or "")


def make_init_wns_fn(sdc, pdk=None, session=None):
    from microsurgeon_flow import pdk_config as pc
    pdk = pdk or pc.NANGATE45

    def init_wns_fn(odb):
        if session is not None:                 # 持久进程: 直接查当前 base WNS
            return session.worst_slack()
        return pc.eval_wns(pdk, Path(odb), sdc)
    return init_wns_fn


def make_read_evidence_fn(sdc, pdk=None, session=None):
    from microsurgeon_flow import pdk_config as pc
    from microsurgeon_flow.finish_rpt_parser import (
        parse_reg2reg_cell_chain, parse_reg2reg_endpoint)
    pdk = pdk or pc.NANGATE45

    def read_evidence_fn(odb):
        # 轨迹本地 report: -format full 不带 -fields(2列, 匹配刀1 parser); PDK 参数化.
        report = (session.report_path() if session is not None
                  else pc.report_path(pdk, Path(odb), sdc))
        synthetic = _TRAJ_SECTION_HEADER + "\n" + report      # 顶部补段头 adapter
        chain = parse_reg2reg_cell_chain(synthetic)
        ep = parse_reg2reg_endpoint(synthetic)
        if ep:
            return PathEvidence(chain, ep["startpoint"], ep["endpoint"], ep["slack"])
        sp = chain[0]["inst"] if chain else ""
        epp = chain[-1]["inst"] if chain else ""
        return PathEvidence(chain, sp, epp, 0.0)              # 端点解析失败兜底
    return read_evidence_fn


def make_eval_fn(sdc, pdk=None, session=None):
    from microsurgeon_flow import pdk_config as pc
    from microsurgeon_flow.backend_eco_oneshot import EcoAction
    pdk = pdk or pc.NANGATE45

    def eval_fn(cand, cur_odb):
        if session is not None:                 # 持久进程: transient 换→评→撤销
            wns = session.eval_swap(cand.target_inst, cand.new_master)
            if wns is None:
                wns = _EVAL_FAIL_WNS
            # cand_odb_path 编码 swap, 供 select 的 commit 钩子复用(base 推进)
            return EvalResult(new_wns=wns, cand_odb_path="session")
        action = EcoAction(action_type="size_cell", target_inst=cand.target_inst,
                           params={"new_master": cand.new_master})
        out_odb, wns = pc.apply_and_eval(pdk, Path(cur_odb), sdc, [action])
        if str(out_odb) == str(cur_odb):     # base_odb = 候选被 reject/未施加
            if wns is None:
                wns = pc.eval_wns(pdk, Path(cur_odb), sdc)   # 真实"无效果"= cur 现状 WNS
            if wns is None:
                wns = _EVAL_FAIL_WNS
            return EvalResult(new_wns=wns, cand_odb_path=str(cur_odb))
        if wns is None:
            wns = _EVAL_FAIL_WNS
        return EvalResult(new_wns=wns, cand_odb_path=str(out_odb))
    return eval_fn


def _call_llm_retry(call_llm, prompt, tries=3):
    """瞬时 API 失败(超时/网络)重试, 防长跑(~100次LLM调用)被瞬时中断打成 abstain.
    tries 次仍 llm_call_error 才返回错误(交调用方降级)."""
    import time as _t
    llm = {}
    for i in range(tries):
        llm = call_llm(prompt)
        if "llm_call_error" not in llm:
            return llm
        if i < tries - 1:
            print(f"[LLM_RETRY] {i+1}/{tries} err={str(llm.get('llm_call_error'))[:60]}")
            _t.sleep(2)
    return llm


def make_propose_fn(period, pdk=None, recall_fn=None):
    from microsurgeon_flow import pdk_config as pc
    from microsurgeon_flow.backend_eco_oneshot import call_llm
    pdk = pdk or pc.NANGATE45

    def propose_fn(evidence, state):
        inst_master = {c["inst"]: c["master"] for c in evidence.cell_chain}
        legal = pc.legal_candidates(pdk, inst_master, pc.lib_cells(pdk))
        recall_text = recall_fn(evidence) if recall_fn else ""   # B: 先检索→喂相似
        llm = _call_llm_retry(call_llm, _build_prompt_propose(
            evidence, legal, state, period,
            legal_mode=pdk.legal_mode, recall_text=recall_text))
        if "llm_call_error" in llm:
            return ProposeResult(is_abstain=True,
                                 abstain_reason=f"llm_error: {llm['llm_call_error']}")
        if llm.get("abstain"):
            return ProposeResult(is_abstain=True,
                                 abstain_reason=llm.get("abstain_reason", "llm abstain"))
        cands = []
        for raw in (llm.get("candidates") or [])[:3]:
            inst, nm = raw.get("target_inst", ""), raw.get("new_master", "")
            if not inst or not nm or nm not in legal.get(inst, []):   # PDK legal 约束
                continue
            klass = pc.action_class(pdk, inst_master.get(inst, ""), nm)
            cands.append(EcoCandidate(inst, nm, klass, raw.get("reason", "")))
        if not cands:
            return ProposeResult(
                is_abstain=True,
                abstain_reason="no legal candidate after pdk legal filter")
        return ProposeResult(candidates=cands)
    return propose_fn


def make_select_fn(on_commit=None):
    reasons: list[str] = []          # 供 distill 读取每步选择理由(对齐 steps 顺序)

    def select_fn(evaluated, state):
        from microsurgeon_flow.backend_eco_oneshot import call_llm
        n = len(evaluated)
        deltas = [ev.new_wns - state.cur_wns for _, ev in evaluated]
        max_idx = max(range(n), key=lambda i: deltas[i])   # 仅日志, 绝不作默认返回(守红线)
        llm = _call_llm_retry(call_llm, _build_prompt_select(evaluated, state))
        if "llm_call_error" in llm:
            reasons.append("(llm_error)")
            print(f"[SELECT] llm_error -> 退 idx0(非max): {llm['llm_call_error']}")
            chosen = 0
        else:
            idx = llm.get("selected_idx")
            if not isinstance(idx, int) or not (0 <= idx < n):
                reasons.append("(invalid_idx)")
                print(f"[SELECT] 非法 selected_idx={idx!r} -> 退 idx0(非max)")
                chosen = 0
            else:
                reason = (llm.get("select_reason") or "").strip()
                if idx != max_idx and not reason:
                    print(f"[SELECT] 选非max(idx={idx})却无理由→契约违例, 仍尊重LLM(不退max)")
                reasons.append(reason or "(no reason)")
                tag = "(投资型非max)" if idx != max_idx else ""
                print(f"[SELECT] idx={idx} max_idx={max_idx} {tag} reason={reason[:80]}")
                chosen = idx
        if on_commit is not None:    # 持久进程: 选中后 commit, base 推进到该候选(eval 时已撤销)
            cand = evaluated[chosen][0]
            on_commit(cand.target_inst, cand.new_master)
        return chosen

    select_fn.reasons = reasons
    return select_fn


def classify_backend_metric(initial_wns: float, final_wns: float) -> str:
    """WNS 驱动的后端三态归类(§3.1/§5.2; 后端域内, 与 loopback 无关).

    closed          : final ≥ 0(VT-swap/sizing 域内闭合).
    routed_unclosed : final < 0 但真改善(部分有效配方, 你说的"有利但没闭合").
    failed          : 无改善(plateau/no-effect, 真负结果).
    """
    if final_wns >= 0:
        return "closed"
    if final_wns > initial_wns + 1e-6:
        return "routed_unclosed"
    return "failed"


def make_distill_fn(design, platform, variant, period, finish_rpt, select_reasons_ref):
    def distill_fn(result):
        from microsurgeon_flow.failed_skill_builder import (
            build_backend_skill_payload, classify_sizing_failure)
        from microsurgeon_flow.finish_rpt_parser import parse_reg2reg_endpoint
        from microsurgeon_flow.three_lib_gate import make_domain_io
        import time
        wns_traj = [s.wns_after for s in result.steps]
        rounds = len(wns_traj)
        if rounds == 0:
            return False, None                       # step1 即 abstain, 无轨迹不蒸
        seq = result.action_class_sequence
        metric = classify_backend_metric(result.initial_wns, result.final_wns)
        # closed 无 failure_class; routed_unclosed/failed 用 §3.6.3 五类
        failure_class = (None if metric == "closed"
                         else classify_sizing_failure(
                             wns_trajectory=wns_traj, rounds=rounds,
                             distinct_strategies=len(set(seq))))
        endpoints = []
        try:
            if finish_rpt and Path(finish_rpt).exists():
                ep = parse_reg2reg_endpoint(Path(finish_rpt).read_text())
                if ep:
                    endpoints = [ep]
        except Exception as exc:
            print(f"[TRAJ_DISTILL] endpoint 采集失败(继续): {exc}")
        case_id = (f"{design}_{platform}_{variant}_"
                   f"p{int(round(period * 100)):03d}_traj_{int(time.time())}")
        # 三态后端技能(后端域内; closed/routed_unclosed/failed 三态明显区分; 不写 loopback)
        payload = build_backend_skill_payload(
            backend_metric=metric, case_id=case_id, design=design,
            platform=platform, variant=variant, poison_param=f"clk_period={period}",
            period=period, initial_wns=result.initial_wns, final_wns=result.final_wns,
            wns_trajectory=wns_traj, action_class_sequence=seq, rounds=rounds,
            select_reasons=[_scrub_wns_literals(r) for r in select_reasons_ref],
            violating_endpoints=endpoints, failure_class=failure_class,
            cross_domain_trigger=(result.outcome == "abstain"),
            abstain_reason=(_scrub_wns_literals(result.abstain_reason)
                            if result.abstain_reason else None))
        mgr, guard = make_domain_io("backend")       # gate1: MEMORY_ROOT 未设→SystemExit
        happened, vet, artifact = guard.vet_distill(mgr, **payload)
        if not happened:
            print(f"[TRAJ_DISTILL] VETO: {vet.veto_code} - {vet.reason}")
        print(f"[TRAJ_DISTILL] backend_metric={metric} outcome={result.outcome} "
              f"distill={happened} skill={getattr(artifact, 'skill_name', None) if happened else None}")
        return happened, (getattr(artifact, "skill_name", None) if happened else None)
    return distill_fn


def _summarize_history(state):
    if not state.steps_so_far:
        return "(空, 第1步)"
    return "\n".join(
        f"step{s.step}: {s.candidates[s.selected_idx].action_class} "
        f"{s.candidates[s.selected_idx].target_inst}->"
        f"{s.candidates[s.selected_idx].new_master} WNS_after={s.wns_after:.4f}"
        for s in state.steps_so_far)


_LEGAL_DESC = {
    "strength": (
        "## 合法 sizing 范围(inst -> 同族双向 masters, 含升档/降档)\n"
        "new_master 必须来自这里; 含 downsize 以腾出非关键 cell 的面积/负载.\n",
        "## 任务: 推理路径性质, 提最多3个 sizing 候选(可含投资型 downsize).\n",
        "约束: new_master∈合法范围; 不跨族; 不无脑全升档(面积有代价).\n",
    ),
    "vt_swap": (
        "## 合法 VT-swap 范围(inst -> 同 cell 换 VT 档: R=RVT/L=LVT/SL=SLVT)\n"
        "VT-swap 不改 drive strength, 同 footprint drop-in. 速度: SLVT>LVT>RVT, "
        "leakage 同序更高. 关键路径换更快档(R->L->SL)提速; 非关键 cell 换慢档(->R)省 leakage.\n"
        "new_master 必须来自这里.\n",
        "## 任务: 推理路径性质, 提最多3个 VT-swap 候选(可含投资型: 先换慢档腾预算).\n",
        "约束: new_master∈合法范围; 不无脑全换最快档(leakage 有代价).\n",
    ),
}


_ABSTAIN_GUIDANCE = (
    "## abstain 准则(严控, 防过早放弃)\n"
    "VT-swap/sizing 是累积多步过程: 单步只换几个 cell(~5-20ps), 但关键路径会随每次 swap "
    "转移、暴露新的可换 cell, 故多步累积改善远大于单步增益(实测可达单步十余倍, gap 看似很大也常能逐步补上). "
    "**不要因'当前 gap 比单步增益大'就 abstain**——那是低估累积潜力.\n"
    "仅在结构性穷尽时才 abstain(置 candidates=[]、回前端): (a)候选已全在最快档/无可用 headroom; "
    "(b)所有候选都不改善 WNS(域内手段试尽); (c)违例本质是结构/约束问题, sizing/VT 不可解. "
    "只要还有候选能改善 WNS, 就继续提候选、别 abstain——何时终止交给轨迹的早停与步数上限, "
    "不由你单步预判.\n"
)


def _build_prompt_propose(evidence, legal, state, period, legal_mode="strength",
                          recall_text=""):
    import json as _json
    legal_hdr, task_line, constraint_line = _LEGAL_DESC.get(
        legal_mode, _LEGAL_DESC["strength"])
    chain = "\n".join(
        f"  {c['inst']}/{c['pin']} ({c['master']}) edge={c['edge']} t={c['time']}"
        for c in evidence.cell_chain)
    recall_block = f"\n{recall_text}\n" if recall_text else ""
    return (
        "你是 post-route 时序修复的轨迹规划专家(非贪心, 可多步投资).\n"
        f"period={period}, 当前 WNS={state.cur_wns:.4f}(负=违规, 目标≥0; 单位随 PDK).\n"
        f"违规端点: {evidence.startpoint} -> {evidence.endpoint}, "
        f"slack={evidence.slack:.4f}.\n\n"
        f"## 关键路径保序 cell 链(launch->capture)\n{chain}\n\n"
        f"{legal_hdr}"
        f"{_json.dumps(legal, ensure_ascii=False, indent=2)}\n"
        f"{recall_block}\n"
        f"## 轨迹历史\n{_summarize_history(state)}\n\n"
        f"{task_line}"
        f"{_ABSTAIN_GUIDANCE}"
        '严格 JSON: {"candidates":[{"target_inst":"...","new_master":"...",'
        '"reason":"..."}],"abstain":false}\n'
        '仅结构性穷尽(见 abstain 准则)才回前端: {"candidates":[],"abstain":true,'
        '"abstain_reason":"..."}\n'
        f"{constraint_line}")


def _select_delta_tag(delta):
    if delta > 1e-6:
        return f"改善 +{delta:.4f}(WNS 变大/更接近0)"
    if delta < -1e-6:
        return f"劣化 {delta:.4f}(WNS 变小/更负)"
    return f"几乎无变化({delta:+.4f})"


def _build_prompt_select(evaluated, state):
    body = "\n".join(
        f"  [{i}] {c.action_class} {c.target_inst}->{c.new_master} "
        f"真实评估后 WNS={ev.new_wns:.4f} → {_select_delta_tag(ev.new_wns - state.cur_wns)} "
        f"提案理由:{c.reason}"
        for i, (c, ev) in enumerate(evaluated))
    return (
        "你在轨迹中选 1 个候选 commit. 下列每个候选的 WNS/ΔWNS 是 OpenROAD 真实评估"
        "(裁判事实, 非预测), 已显式标注'改善/劣化', 请勿误读符号.\n"
        "符号约定: WNS 为负代表违规; ΔWNS>0=改善(更接近0), ΔWNS<0=劣化(更负).\n"
        f"当前 WNS={state.cur_wns:.4f}.\n\n## 候选(含真实评估结果)\n{body}\n\n"
        "## 规则:\n"
        "1. 默认选'改善'幅度最大的候选.\n"
        "2. 可选改善非最大、甚至当前'劣化'的'投资型'候选(如先 downsize 腾空间), 但选"
        "非最大时 select_reason 必须给出'后续具体如何赚回'的多步理由.\n"
        "3. select_reason 必须如实陈述所选候选的真实方向(改善还是劣化), 不得把劣化说成改善.\n"
        '严格 JSON: {"selected_idx": <int>, "select_reason": "..."}\n')
