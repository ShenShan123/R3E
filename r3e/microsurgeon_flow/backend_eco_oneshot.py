"""
backend_eco_oneshot.py — 蓝队主动 ECO 修复执行体（post-route, §8.2 Step4 B第三刀）。
Part 1: OpenROAD 评估后端 + 单步 replace_cell 施加 + commit 回退骨架。不接 LLM（Part 2 加）。
口径: read_db + setRC + estimate_parasitics + STA, 与 ORFS finish 同口径(gcd 0.46 = -0.06)。
动作原语: 单步 replace_cell（避开崩溃的 repair_timing/pin_swap/insert_buffer）。评估 SDC 恒为原 0.46。
"""
from __future__ import annotations
import os, subprocess, tempfile, shutil, re, time
from pathlib import Path
from dataclasses import dataclass, field

ORFS = Path(os.environ.get("ORFS_ROOT", "/path/to/OpenROAD-flow-scripts")) / "flow"
PLAT = ORFS / "platforms/nangate45"
TECH_LEF  = PLAT / "lef/NangateOpenCellLibrary.tech.lef"
MACRO_LEF = PLAT / "lef/NangateOpenCellLibrary.macro.lef"

# ── Step 5a 桩常量(仅剩 PnR re-close 桩; failure_class 路由已实装) ──────────
LIB       = PLAT / "lib/NangateOpenCellLibrary_typical.lib"
SETRC     = PLAT / "setRC.tcl"
OPENROAD  = "/usr/bin/openroad"

# 红线: 执行体只允许这两类(收窄 agentic_eco_engine 的 5 类全局白名单)。
# pin_swap/insert_buffer/tool_repair 在此 odb 系统性 crash 且属红线风险 → 硬拒。
ALLOWED_ACTIONS = {"size_cell", "resize_chain"}


@dataclass
class EcoAction:
    action_type: str                       # "size_cell" | "resize_chain"
    target_inst: str = ""
    target_insts: list = None
    params: dict = field(default_factory=dict)


def _run_openroad(tcl: str, timeout: int = 120) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as f:
        f.write(tcl)
        tcl_path = f.name
    env = {**os.environ, "IN_NIX_SHELL": "1"}
    try:
        r = subprocess.run([OPENROAD, "-no_splash", "-exit", tcl_path],
                           capture_output=True, text=True, timeout=timeout, env=env)
        return r.stdout + "\n" + r.stderr
    finally:
        os.unlink(tcl_path)


def _preamble(odb: Path, sdc: Path) -> str:
    return (
        f"read_lef {TECH_LEF}\n"
        f"read_lef {MACRO_LEF}\n"
        f"read_liberty {LIB}\n"
        f"read_db {odb}\n"
        f"read_sdc {sdc}\n"
        f"source {SETRC}\n"
        f"estimate_parasitics -placement\n"
    )


_WNS_RE = re.compile(r"WNS_EVAL:\s*([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)")


def eval_wns(odb: Path, sdc: Path) -> float | None:
    """post-route WNS 评估(与 finish 同口径)。解析自证: 无 worst slack 行返 None。"""
    tcl = _preamble(odb, sdc) + 'puts "WNS_EVAL: [sta::worst_slack -max]"\nexit\n'
    out = _run_openroad(tcl)
    m = _WNS_RE.search(out)
    return float(m.group(1)) if m else None


def get_critical_path_report(odb: Path, sdc: Path) -> str:
    """report_checks 文本(喂 LLM 上下文 / 提取 instance+master)。"""
    tcl = _preamble(odb, sdc) + "report_checks -path_delay max -format full -fields {slew cap}\nexit\n"
    return _run_openroad(tcl)


def apply_and_eval(base_odb: Path, sdc: Path, actions: list[EcoAction]) -> tuple[Path, float | None]:
    """
    在 base_odb 副本上施加 replace_cell, 重评估。返回 (改后odb副本, 新WNS)。原 odb 不动。
    红线: 只施加 ALLOWED_ACTIONS; new_master 必须非空。
    """
    work = Path(tempfile.mkdtemp(prefix="eco_apply_"))
    cand_odb = work / "cand.odb"

    legal = [a for a in actions if a.action_type in ALLOWED_ACTIONS]
    if len(legal) != len(actions):
        print(f"[红线] 过滤掉 {len(actions) - len(legal)} 个非白名单动作")

    cmds = []
    for a in legal:
        if a.action_type == "size_cell":
            nm = a.params.get("new_master")
            if a.target_inst and nm:
                # Tcl {} quoting: prevents $var and [cmd] substitution in inst names
                cmds.append(f"replace_cell {{{a.target_inst}}} {nm}")
        elif a.action_type == "resize_chain":
            insts = a.target_insts or a.params.get("chain_insts", []) or []
            masters = a.params.get("target_masters", []) or []
            for inst, nm in zip(insts, masters):
                if inst and nm:
                    cmds.append(f"replace_cell {{{inst}}} {nm}")

    if not cmds:
        shutil.rmtree(work, ignore_errors=True)
        return base_odb, None

    tcl = (_preamble(base_odb, sdc)
           + "\n".join(cmds) + "\n"
           + "estimate_parasitics -placement\n"
           + f"write_db {cand_odb}\n"
           + 'puts "WNS_EVAL: [sta::worst_slack -max]"\n'
           + "exit\n")
    out = _run_openroad(tcl)
    m = _WNS_RE.search(out)
    wns = float(m.group(1)) if m else None
    if not cand_odb.exists():
        shutil.rmtree(work, ignore_errors=True)
        return base_odb, wns
    return cand_odb, wns


# ─── Part 2: LLM (DeepSeek) 集成 + run_eco_repair ──────────────────────────
import json
from datetime import datetime

try:
    from openai import OpenAI as _OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

_DEFAULT_ALLOWED_EGRESS_HOSTS: frozenset = frozenset({
    "api.deepseek.com", "api.deepseek.com:443",
})
_DEFAULT_BASE   = "https://api.deepseek.com"
_DEFAULT_MODEL  = "deepseek-v4-flash"   # env DEEPSEEK_MODEL overrides at call time
_DEFAULT_TIMEOUT = int(os.environ.get("ECO_LLM_TIMEOUT", "60"))
_LLM_MAX_RETRIES = int(os.environ.get("ECO_LLM_RETRIES", "4"))
_LLM_BACKOFF     = float(os.environ.get("ECO_LLM_BACKOFF", "5"))


def _extract_json(text: str | None) -> dict:
    """Tolerant JSON extraction: handles ```json fences and surrounding prose."""
    if not text:
        return {"llm_call_error": "empty LLM response"}
    s = text.strip()
    # strip inline reasoning blocks some models emit (e.g. MiniMax <think>...</think>)
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL | re.IGNORECASE).strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
        s = s.removeprefix("json").strip()
    try:
        return json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                pass
    return {"llm_call_error": f"non-JSON LLM response: {text[:120]!r}"}

_FAMILY_RE      = re.compile(r"^(.+_X)(\d+)$")
_CELL_LIB_RE    = re.compile(r"^\s*cell\s*\(([^)\"]+)\)")
_REPORT_CELL_RE = re.compile(r"[v^]\s+(\S+)/\S+\s+\((\S+)\)")


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse `export KEY=VALUE` or `KEY=VALUE` lines; skip comments."""
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export").strip()
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _allowed_egress_hosts() -> frozenset[str]:
    """Return an explicit allowlist without embedding private gateways."""
    configured = {
        value.strip()
        for value in os.environ.get("R3E_ALLOWED_LLM_HOSTS", "").split(",")
        if value.strip()
    }
    return frozenset(_DEFAULT_ALLOWED_EGRESS_HOSTS | configured)


def _egress_guard(base_url: str) -> None:
    """Require HTTPS and an explicitly allowed provider host."""
    import urllib.parse
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "https":
        raise RuntimeError(f"egress guard: scheme must be https, got {parsed.scheme!r}")
    if parsed.netloc not in _allowed_egress_hosts():
        raise RuntimeError(
            f"egress guard: {parsed.netloc!r} is not explicitly allowed"
        )


def _get_all_lib_cells() -> set[str]:
    cells: set[str] = set()
    with open(LIB) as f:
        for line in f:
            m = _CELL_LIB_RE.match(line)
            if m:
                cells.add(m.group(1).strip())
    return cells


def _parse_inst_master_from_report(report: str) -> dict[str, str]:
    """从 report_checks 文本提取 inst→master（同一 inst 多次出现取最后一次）。"""
    inst_master: dict[str, str] = {}
    for line in report.splitlines():
        m = _REPORT_CELL_RE.search(line)
        if m:
            inst_master[m.group(1)] = m.group(2)
    return inst_master


def _cell_strength(master: str) -> int | None:
    m = _FAMILY_RE.match(master)
    return int(m.group(2)) if m else None


def _build_scoped_legal(
    inst_master_map: dict[str, str], all_lib_cells: set[str]
) -> dict[str, list[str]]:
    """
    为每个 inst 建同族升档列表（strictly higher drive strength, same prefix）。
    跨族替换（如 NAND→INV）在此结构中天然不可能出现。
    """
    scoped: dict[str, list[str]] = {}
    for inst, current in inst_master_map.items():
        fm = _FAMILY_RE.match(current)
        if not fm:
            scoped[inst] = []
            continue
        prefix, cur_str = fm.group(1), int(fm.group(2))
        upgrades = sorted(
            [c for c in all_lib_cells
             if c.startswith(prefix) and _cell_strength(c) is not None
             and _cell_strength(c) > cur_str],
            key=_cell_strength,  # type: ignore[arg-type]
        )
        scoped[inst] = upgrades
    return scoped


def _filter_actions(
    actions: list[EcoAction],
    scoped_legal: dict[str, list[str]],
    inst_master_map: dict[str, str],
) -> tuple[list[EcoAction], list[str]]:
    """
    红线第2层：action_type 白名单 + new_master ∈ 该 inst 同族升档（scoped_legal）。
    不是"全局任意合法 master"，而是"这个 inst 当前 master 的同族升档"。
    """
    kept: list[EcoAction] = []
    rejected: list[str]   = []
    for a in actions:
        if a.action_type not in ALLOWED_ACTIONS:
            rejected.append(f"[红线] {a.action_type} 非白名单")
            continue
        if a.action_type == "size_cell":
            inst = a.target_inst
            nm   = a.params.get("new_master", "")
            if not inst or not nm:
                rejected.append("[过滤] size_cell: inst 或 new_master 为空")
                continue
            legal = scoped_legal.get(inst, [])
            if nm not in legal:
                cur = inst_master_map.get(inst, "?")
                rejected.append(f"[红线] {inst}: {cur}→{nm} 非同族升档 legal={legal}")
                continue
            kept.append(a)
        elif a.action_type == "resize_chain":
            insts   = a.target_insts or a.params.get("chain_insts", []) or []
            masters = a.params.get("target_masters", []) or []
            ok_insts: list[str] = []
            ok_masters: list[str] = []
            for inst, nm in zip(insts, masters):
                legal = scoped_legal.get(inst, [])
                if nm in legal:
                    ok_insts.append(inst)
                    ok_masters.append(nm)
                else:
                    cur = inst_master_map.get(inst, "?")
                    rejected.append(f"[红线] {inst}: {cur}→{nm} 非同族升档 legal={legal}")
            if ok_insts:
                kept.append(EcoAction(
                    action_type="resize_chain",
                    target_insts=ok_insts,
                    params={**a.params, "chain_insts": ok_insts, "target_masters": ok_masters},
                ))
    return kept, rejected


def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """
    OpenAI-compatible JSON call. Credentials and provider configuration are
    read only from environment variables; repository files are never used.
    """
    if not _HAS_OPENAI:
        return {"llm_call_error": "openai package not installed; pip install openai"}
    api_key  = (os.environ.get("LLM_API_KEY")
                or os.environ.get("DEEPSEEK_API_KEY", ""))
    resolved_model = (model
                      or os.environ.get("LLM_MODEL")
                      or os.environ.get("DEEPSEEK_MODEL", _DEFAULT_MODEL))
    resolved_base  = (base_url
                      or os.environ.get("LLM_BASE_URL")
                      or os.environ.get("DEEPSEEK_BASE_URL", _DEFAULT_BASE)).rstrip("/")
    # tolerate base URLs that already include the endpoint path (the OpenAI SDK
    # appends /chat/completions itself, so a trailing one would double up)
    if resolved_base.endswith("/chat/completions"):
        resolved_base = resolved_base[: -len("/chat/completions")].rstrip("/")
    if not api_key:
        return {"llm_call_error": "no API key (set LLM_API_KEY or DEEPSEEK_API_KEY)"}
    try:
        _egress_guard(resolved_base)
    except RuntimeError as exc:
        return {"llm_call_error": str(exc)}
    client = _OpenAI(api_key=api_key, base_url=resolved_base, timeout=timeout)

    # Fixed sampling controls for cross-model experiments (set by the runner via
    # env, recorded into run_config). Degrade gracefully if a backbone rejects them.
    extra: dict = {}
    _temp = os.environ.get("LLM_TEMPERATURE")
    _maxt = os.environ.get("LLM_MAX_TOKENS")
    _seed = os.environ.get("LLM_SEED")
    if _temp not in (None, ""):
        try:
            extra["temperature"] = float(_temp)
        except ValueError:
            pass
    if _maxt not in (None, ""):
        try:
            extra["max_tokens"] = int(_maxt)
        except ValueError:
            pass
    if _seed not in (None, ""):
        try:
            extra["seed"] = int(_seed)
        except ValueError:
            pass
    # provider-specific extra_body (e.g. GLM `{"thinking":{"type":"disabled"}}`);
    # set by the runner only for the relevant provider, recorded into run_config.
    _eb = os.environ.get("LLM_EXTRA_BODY")
    if _eb not in (None, ""):
        try:
            extra["extra_body"] = json.loads(_eb)
        except (ValueError, TypeError):
            pass

    def _once() -> dict:
        base_kw = {"model": resolved_model,
                   "messages": [{"role": "user", "content": prompt}]}
        # Capability-degrade matrix: prefer json mode + fixed sampling; on a
        # capability error drop to a less-featured variant. Rate/transient errors
        # bubble up to the outer backoff loop instead.
        # models known to not support json mode (gateway returns slow 429 when forced)
        _NO_JSON = {"kimi-k2.7-code", "MiniMax-M3", "glm-5.2"}
        can_json = resolved_model not in _NO_JSON
        variants = []
        if can_json:
            variants.append({**base_kw, "response_format": {"type": "json_object"}, **extra})
        variants.append({**base_kw, **extra})
        if can_json:
            variants.append({**base_kw, "response_format": {"type": "json_object"}})
        variants.append(dict(base_kw))
        last_exc: Exception | None = None
        for kw in variants:
            try:
                resp = client.chat.completions.create(**kw)
                return _extract_json(resp.choices[0].message.content)
            except Exception as exc:
                last_exc = exc
                low = str(exc).lower()
                # capability errors first: some gateways (glm) report
                # "json mode not supported" *with* HTTP 429, so this must take
                # precedence over the transient check below.
                capability = (("json" in low and ("support" in low or "not_found" in low))
                              or "unsupported" in low
                              or ("param" in low and "support" not in low))
                if capability:
                    continue  # drop to next, less-featured variant
                if any(t in low for t in ("rate limit", "rate_limit", "saturat",
                                          "饱和", "timeout", "timed out",
                                          "overload", "529")):
                    raise  # transient -> outer backoff
                if "429" in low:
                    raise  # generic 429 (true rate limit) -> outer backoff
                continue   # other capability/format issue -> next variant
        raise last_exc if last_exc else RuntimeError("unreachable")

    last = ""
    for attempt in range(_LLM_MAX_RETRIES):
        try:
            return _once()
        except Exception as exc:
            last = str(exc)
            low = last.lower()
            if any(t in low for t in ("429", "rate", "saturat", "饱和", "timeout", "timed out")):
                time.sleep(_LLM_BACKOFF * (attempt + 1))
                continue
            break
    return {"llm_call_error": last}


def _build_backend_preflight(
    skill_lib: Path | None,
    *,
    design: str,
    platform: str,
    period: float | None,
    endpoint: str = "",
    recall_k: int = 3,
    recall_candidate_k: int = 12,
    enable_template_preflight: bool = False,
) -> tuple[dict, dict[str, list[dict]]]:
    """Retrieve backend skills for pre-LLM action attempts.

    The deterministic/template patch path is deliberately a reserved interface
    here: recalled memories are not injected into the LLM prompt. They are used
    only to derive guarded candidate actions, which must pass STA before commit.
    """
    template_attempt = {
        "enabled": bool(enable_template_preflight),
        "status": "reserved_no_registered_template"
        if enable_template_preflight else "disabled",
        "hit": False,
    }
    if not skill_lib:
        return {"enabled": False, "template_attempt": template_attempt}, {}
    if period is None:
        return {
            "enabled": False,
            "reason": "missing_period",
            "skill_lib": str(skill_lib),
            "template_attempt": template_attempt,
        }, {}

    from microsurgeon_flow.skill_recall import recall_skills

    recalled = recall_skills(
        skill_lib,
        design=design,
        platform=platform,
        period=period,
        endpoint=endpoint,
        k=recall_k,
        candidate_k=recall_candidate_k,
    )
    hits = {state: len(items) for state, items in recalled.items() if items}
    return {
        "enabled": True,
        "skill_lib": str(skill_lib),
        "recall_k": int(recall_k),
        "recall_candidate_k": int(recall_candidate_k),
        "endpoint": endpoint,
        "hits": hits,
        "template_attempt": template_attempt,
    }, recalled


def _skill_action_classes(skill: dict) -> list[str]:
    fs = skill.get("precondition", {}).get("failure_signature", {})
    at = skill.get("action_template", {})
    seq = fs.get("action_class_sequence") or at.get("key_actions") or []
    return [str(x) for x in seq if x]


def _derive_preflight_actions(
    recalled: dict[str, list[dict]],
    scoped_legal: dict[str, list[str]],
    *,
    max_actions: int = 1,
) -> tuple[list[EcoAction], list[dict]]:
    """Convert recalled positive strategies into guarded concrete ECO actions.

    The memory stores strategy classes, not instance-specific edits. We therefore
    instantiate only the conservative action currently supported by this ECO
    loop: size the earliest critical-path instance that has a legal same-family
    upgrade. Failed skills are negative evidence and never generate actions.
    """
    actions: list[EcoAction] = []
    sources: list[dict] = []
    used: set[str] = set()
    positive = list(recalled.get("closed", [])) + list(recalled.get("routed_unclosed", []))
    for skill in positive:
        classes = _skill_action_classes(skill)
        strategy = str(skill.get("action_template", {}).get("repair_strategy", ""))
        wants_sizing = (
            any(c in {"vt_faster", "upsize", "size_cell", "resize_chain"} for c in classes)
            or "vtswap" in strategy
            or "repair_timing" in strategy
            or "size" in strategy
        )
        if not wants_sizing:
            continue
        for inst, legal in scoped_legal.items():
            if inst in used or not legal:
                continue
            target = legal[0]
            actions.append(EcoAction(
                action_type="size_cell",
                target_inst=inst,
                params={"new_master": target},
            ))
            sources.append({
                "skill_name": skill.get("skill_name"),
                "state": skill.get("validation", {}).get("backend_metric")
                or skill.get("precondition", {}).get("backend_outcome"),
                "strategy": strategy,
                "action_classes": classes[:5],
            })
            used.add(inst)
            break
        if len(actions) >= max_actions:
            break
    return actions, sources


def _build_prompt(report: str, scoped_legal: dict[str, list[str]], wns: float) -> str:
    scoped_json = json.dumps(scoped_legal, ensure_ascii=False, indent=2)
    return (
        f"你是 post-route ECO 修复专家。当前 WNS = {wns:.4f} ns（负值代表违规，目标 ≥ 0）。\n\n"
        f"## 关键路径报告\n{report}\n\n"
        "## 合法升档范围（inst → 同族升档 masters）\n"
        "注意：new_master 必须来自这里，不得跨族替换（如 NAND→INV 是非法的）。\n"
        f"{scoped_json}\n\n"
        "## 任务\n"
        "输出 JSON，严格格式：\n"
        '{"actions": [{"action_type": "size_cell", "target_inst": "INST_NAME", '
        '"params": {"new_master": "MASTER_NAME"}}, ...], "reasoning": "..."}\n\n'
        "约束：\n"
        "1. action_type 只允许 size_cell 或 resize_chain。\n"
        "2. new_master 必须来自合法升档范围（同族更高驱动强度），不得跨族。\n"
        "3. 优先修关键路径最慢的 inst，不要盲目升档所有 inst（面积有代价）。\n"
        '4. 若无可操作 inst，返回 {"actions": [], "reasoning": "no actionable cells"}。\n'
    )


def _make_eco_action(raw: dict) -> "EcoAction | None":
    try:
        fields = {k: v for k, v in raw.items() if k in EcoAction.__dataclass_fields__}
        return EcoAction(**fields)
    except Exception:
        return None


def run_eco_repair(
    odb: Path,
    sdc: Path,
    *,
    baseline_wns: float = -0.06,   # finish 口径参考基线，仅记录，不参与增量计算
    max_iter: int = 3,
    artifacts_dir: Path | None = None,
    # ── 失败技能蒸馏所需元信息(§3.6.2);全 optional,缺则以 "unknown" 兜底 ──
    design: str = "unknown",
    platform: str = "unknown",
    variant: str = "base",
    period: float | None = None,
    case_id: str | None = None,
    skill_lib: Path | None = None,
    template_lib: Path | None = None,
    pattern_template_lib: Path | None = None,
    enable_skill_preflight: bool = True,
    recall_k: int = 3,
    recall_candidate_k: int = 12,
    preflight_max_actions: int = 3,
    enable_template_preflight: bool = False,
) -> dict:
    """
    LLM 驱动的 post-route ECO 修复主循环。

    P2 修正：increment_vs_initial = final_wns - initial_wns（两者均为实测）。
             baseline_wns 仅作参考记录，不参与任何增量计算（防假增量）。
    P3 修正：接受的 odb 复制到 artifacts/accepted_iterN.odb；回退的 tmp 目录即时 rmtree。
    is_repair_increment=True：标记为蓝队主动修复尝试（不论有无真增量，数据诚实）。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if artifacts_dir is None:
        artifacts_dir = Path("artifacts") / f"backend_eco_llm_{ts}"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    resolved_model = os.environ.get("DEEPSEEK_MODEL", _DEFAULT_MODEL)
    resolved_base = os.environ.get("DEEPSEEK_BASE_URL", _DEFAULT_BASE).rstrip("/")
    print(f"[egress 自证] DeepSeek base={resolved_base}  model={resolved_model}  timeout={_DEFAULT_TIMEOUT}s")

    # P2：实测初始 WNS 作为增量基准，不用写死的 baseline_wns
    initial_wns = eval_wns(odb, sdc)
    if initial_wns is None:
        return {"status": "eval_failed", "initial_wns": None}
    print(f"[初始 WNS] {initial_wns:.4f} ns  (finish 参考基线 {baseline_wns:.4f} ns，仅参考)")

    all_lib_cells = _get_all_lib_cells()
    current_odb   = odb
    final_wns     = initial_wns
    history: list[dict] = []

    for it in range(1, max_iter + 1):
        print(f"\n── iter {it}/{max_iter} ──")
        report          = get_critical_path_report(current_odb, sdc)
        inst_master_map = _parse_inst_master_from_report(report)
        scoped_legal    = _build_scoped_legal(inst_master_map, all_lib_cells)
        recalled: dict[str, list[dict]] = {"closed": [], "routed_unclosed": [], "failed": []}
        preflight_rec, recalled = _build_backend_preflight(
            Path(skill_lib) if (enable_skill_preflight and skill_lib) else None,
            design=design,
            platform=platform,
            period=period,
            recall_k=recall_k,
            recall_candidate_k=recall_candidate_k,
            enable_template_preflight=enable_template_preflight,
        )
        preflight_rec["skill_preflight_enabled"] = bool(enable_skill_preflight and skill_lib)
        template_actions: list[EcoAction] = []
        template_sources: list[dict] = []
        if enable_template_preflight:
            try:
                from microsurgeon_flow.backend_template_preflight import (
                    generate_pattern_actions,
                    generate_template_actions,
                    load_pattern_template_file,
                    load_template_file,
                    select_templates,
                )
                if pattern_template_lib:
                    patterns = load_pattern_template_file(pattern_template_lib)
                    template_actions, template_sources = generate_pattern_actions(
                        patterns,
                        scoped_legal,
                        inst_master_map,
                        max_actions=preflight_max_actions,
                    )
                    preflight_rec["template_attempt"] = {
                        **preflight_rec.get("template_attempt", {}),
                        "enabled": True,
                        "kind": "pattern_template",
                        "library": str(pattern_template_lib),
                        "patterns_loaded": len(patterns),
                        "candidate_count": len(template_actions),
                    }
                elif template_lib:
                    templates = load_template_file(template_lib)
                    selected_templates = select_templates(
                        templates,
                        design=design,
                        platform=platform,
                        period=period,
                        max_templates=3,
                    )
                    template_actions, template_sources = generate_template_actions(
                        selected_templates,
                        scoped_legal,
                        inst_master_map,
                        max_actions=preflight_max_actions,
                    )
                    preflight_rec["template_attempt"] = {
                        **preflight_rec.get("template_attempt", {}),
                        "enabled": True,
                        "kind": "distilled_template",
                        "library": str(template_lib),
                        "templates_loaded": len(templates),
                        "templates_selected": [
                            t.get("template_id") for t in selected_templates
                        ],
                        "candidate_count": len(template_actions),
                    }
            except Exception as exc:  # noqa: BLE001
                preflight_rec["template_attempt"] = {
                    **preflight_rec.get("template_attempt", {}),
                    "enabled": True,
                    "status": "template_error",
                    "error": str(exc),
                }

        skill_actions, skill_sources = _derive_preflight_actions(
            recalled,
            scoped_legal,
            max_actions=max(0, preflight_max_actions - len(template_actions)),
        )
        preflight_actions: list[EcoAction] = []
        preflight_sources: list[dict] = []
        seen_actions: set[tuple[str, str]] = set()
        for action, source in list(zip(template_actions, template_sources)) + list(zip(skill_actions, skill_sources)):
            key = (action.target_inst, str(action.params.get("new_master", "")))
            if key in seen_actions:
                continue
            seen_actions.add(key)
            preflight_actions.append(action)
            preflight_sources.append(source)
            if len(preflight_actions) >= preflight_max_actions:
                break
        preflight_kept, preflight_rejected = _filter_actions(
            preflight_actions,
            scoped_legal,
            inst_master_map,
        )
        preflight_rec["candidate_actions"] = [
            {"action_type": a.action_type, "target_inst": a.target_inst,
             "to_master": a.params.get("new_master"),
             "source": "pattern_template" if a.params.get("pattern_id")
             else ("distilled_template" if a.params.get("template_id") else "skill_recall"),
             "pattern_id": a.params.get("pattern_id"),
             "template_id": a.params.get("template_id")}
            for a in preflight_actions
        ]
        preflight_rec["candidate_sources"] = preflight_sources
        preflight_rec["rejected"] = preflight_rejected
        if preflight_kept:
            attempts = []
            best = None
            for idx, action in enumerate(preflight_kept):
                cand_odb, new_wns = apply_and_eval(current_odb, sdc, [action])
                attempt = {
                    "idx": idx,
                    "action": {
                        "action_type": action.action_type,
                        "target_inst": action.target_inst,
                        "to_master": action.params.get("new_master"),
                        "pattern_id": action.params.get("pattern_id"),
                        "template_id": action.params.get("template_id"),
                    },
                    "wns": new_wns,
                    "delta": None if new_wns is None else new_wns - final_wns,
                    "odb": str(cand_odb) if cand_odb != current_odb else None,
                }
                attempts.append(attempt)
                if new_wns is not None and (best is None or new_wns > best["wns"]):
                    best = {"idx": idx, "action": action, "wns": new_wns, "odb": cand_odb}
            preflight_rec["attempts"] = attempts
            if best is None:
                preflight_rec["attempt_status"] = "eval_failed"
            else:
                new_wns = best["wns"]
                delta = new_wns - final_wns
                improved = new_wns > final_wns + 1e-6
                preflight_rec["attempt_status"] = (
                    "accepted" if improved else "reverted"
                )
                preflight_rec["attempt_wns"] = new_wns
                preflight_rec["attempt_delta"] = delta
                preflight_rec["selected_attempt_idx"] = best["idx"]
                if improved:
                    accepted_path = artifacts_dir / f"accepted_iter{it}_preflight.odb"
                    shutil.copy2(best["odb"], accepted_path)
                    for attempt in attempts:
                        p = attempt.get("odb")
                        if p:
                            shutil.rmtree(Path(p).parent, ignore_errors=True)
                    current_odb = accepted_path
                    final_wns = new_wns
                    action = best["action"]
                    _preflight_payload = {
                        "actions": [{
                            "action_type": action.action_type,
                            "target_inst": action.target_inst,
                            "to_master": action.params.get("new_master"),
                            "pattern_id": action.params.get("pattern_id"),
                            "template_id": action.params.get("template_id"),
                        }],
                        "action_count": 1,
                    }
                    history.append({
                        "iter": it,
                        "wns": new_wns,
                        "delta": delta,
                        "status": "accepted",
                        "repair_source": "preflight_skill",
                        "odb": accepted_path.name,
                        "preflight": preflight_rec,
                        **_preflight_payload,
                    })
                    print(
                        f"  ✓ preflight commit → {accepted_path.name} "
                        f"WNS {new_wns:.4f} (Δ={delta:+.4f})"
                    )
                    continue
            for attempt in attempts:
                p = attempt.get("odb")
                if p:
                    shutil.rmtree(Path(p).parent, ignore_errors=True)
        else:
            preflight_rec["attempt_status"] = "no_candidate_action"

        prompt = _build_prompt(report, scoped_legal, final_wns)
        print(f"  → call_llm  model={resolved_model}  scoped_insts={len(scoped_legal)}")
        llm = call_llm(prompt)

        if "llm_call_error" in llm:
            print(f"  ✗ LLM 错误: {llm['llm_call_error']}")
            history.append({"iter": it, "error": llm["llm_call_error"],
                            "actions": [], "action_count": 0,
                            "preflight": preflight_rec})
            break

        raw_actions = [a for a in (_make_eco_action(r) for r in llm.get("actions", [])) if a]
        kept, rejected = _filter_actions(raw_actions, scoped_legal, inst_master_map)
        for msg in rejected:
            print(f"  {msg}")

        # A-1: action summary — 必须在 kept 定义之后构建；
        #      LLM 错误出口在此之前 break，已手填 actions=[]
        _act_payload = {
            "actions": [
                {"action_type": a.action_type, "target_inst": a.target_inst,
                 "to_master": a.params.get("new_master")}
                for a in kept
            ],
            "action_count": len(kept),
        }

        if not kept:
            print("  → 无合法动作，停止")
            history.append({"iter": it, "kept": 0, "wns": final_wns, "status": "no_actions",
                            "reasoning": llm.get("reasoning", ""),
                            "preflight": preflight_rec,
                            **_act_payload})
            break

        cand_odb, new_wns = apply_and_eval(current_odb, sdc, kept)

        if new_wns is None:
            print("  ✗ WNS 解析失败，回退")
            if cand_odb != current_odb:
                shutil.rmtree(cand_odb.parent, ignore_errors=True)
            history.append({"iter": it, "wns": None, "status": "eval_failed",
                            "preflight": preflight_rec,
                            **_act_payload})
            continue

        delta = new_wns - final_wns
        print(f"  WNS: {final_wns:.4f} → {new_wns:.4f}  (Δ={delta:+.4f})")

        if new_wns >= final_wns:
            # P3: commit → 落 artifacts，清 tmp
            accepted_path = artifacts_dir / f"accepted_iter{it}.odb"
            shutil.copy2(cand_odb, accepted_path)
            shutil.rmtree(cand_odb.parent, ignore_errors=True)   # 清 tmp 副本
            current_odb = accepted_path
            final_wns   = new_wns
            history.append({"iter": it, "wns": new_wns, "delta": delta,
                            "status": "accepted", "odb": accepted_path.name,
                            "preflight": preflight_rec,
                            **_act_payload})
            print(f"  ✓ commit → {accepted_path.name}")
        else:
            # P3: revert → 清 tmp
            shutil.rmtree(cand_odb.parent, ignore_errors=True)
            history.append({"iter": it, "wns": new_wns, "delta": delta, "status": "reverted",
                            "preflight": preflight_rec,
                            **_act_payload})
            print("  ✗ revert (无改善)")

    # P2: 增量 = 实测 final - 实测 initial（不用写死的 baseline_wns）
    increment = final_wns - initial_wns
    result = {
        "status":               "routed_unclosed" if final_wns < 0 else "closed",
        "initial_wns":          initial_wns,
        "final_wns":            final_wns,
        "baseline_wns_ref":     baseline_wns,        # finish 口径参考，不参与计算
        "increment_vs_initial": increment,           # 实测增量：LLM 改后 vs 改前同口径
        "is_repair_increment":  True,                # 蓝队主动修复尝试标记
        "final_odb":            str(current_odb) if current_odb != odb else None,
        "artifacts_dir":        str(artifacts_dir),
        "history":              history,
    }
    # ── Telemetry: 蒸馏/回退状态暴露, 零行为变化 ────────────────────
    result["_distill_happened"] = False
    result["skill_name"] = None
    result["failure_class"] = None       # §4.3 四类路由消费; 无失败时 None → 不回滚
    result["violating_endpoints"] = []
    print(f"\n[结果] {result['status']}  increment={increment:+.4f}  final={final_wns:.4f}")

    # ── §3.6.2 失败技能蒸馏(整轮无改善才产) ─────────────────────────────
    # 触发条件: final_wns <= initial_wns + 1e-6 (无显著改善)
    # 容错:design/platform/period 缺则跳过(避免 "unknown" 污染真库;
    #       由 driver 注入元信息后才落库)
    if final_wns <= initial_wns + 1e-6 and design != "unknown" and period is not None:
        happened = False
        artifact = None
        try:
            from microsurgeon_flow.failed_skill_builder import (
                build_failed_skill_payload,
                classify_sizing_failure,
                derive_strategy_label,
            )
            from microsurgeon_flow.finish_rpt_parser import parse_reg2reg_endpoint
            from microsurgeon_flow.three_lib_gate import make_domain_io

            wns_traj = [h["wns"] for h in history if h.get("wns") is not None]
            rounds = len(wns_traj)
            # B: 从 A-2 标签算真去重计数, 不再 =rounds 取巧
            _strategy_labels = [derive_strategy_label(h) for h in history]
            _distinct_strategy_count = len(set(_strategy_labels))
            failure_class = classify_sizing_failure(
                wns_trajectory=wns_traj,
                rounds=rounds,
                distinct_strategies=_distinct_strategy_count,  # API 兼容, 不再参与路由
            )
            result["failure_class"] = failure_class  # 刀一: 尽早写入, 防后续异常丢失

            # 端点采集:容错读 6_finish.rpt,缺/解析失败用 []
            endpoints: list = []
            try:
                period_int = int(round(period * 100))
                finish_rpt = (
                    ORFS / "reports" / platform / f"{design}_p{period_int:03d}"
                    / variant / "6_finish.rpt"
                )
                if finish_rpt.exists():
                    ep = parse_reg2reg_endpoint(finish_rpt.read_text())
                    if ep:
                        endpoints = [ep]
            except Exception as exc:
                print(f"[FAILED_SKILL] endpoint 采集失败(继续): {exc}")

            # 覆盖 telemetry 默认值(使 return dict 携带真实采集结果)
            result["violating_endpoints"] = endpoints

            # case_id 自动产
            case_id_final = case_id or (
                f"{design}_{platform}_{variant}_p{int(round(period * 100)):03d}_"
                f"failed_{int(datetime.now().timestamp())}"
            )

            payload = build_failed_skill_payload(
                case_id=case_id_final,
                design=design,
                platform=platform,
                variant=variant,
                poison_param=f"clk_period={period}",
                period=period,
                failure_class=failure_class,
                wns_trajectory=wns_traj,
                strategy_tried=[derive_strategy_label(h) for h in history],
                rounds=rounds,
                violating_endpoints=endpoints,
                distinct_strategy_count=_distinct_strategy_count,  # B: 证据透传
            )

            mgr, guard = make_domain_io("backend")
            happened, vet, artifact = guard.vet_distill(mgr, **payload)
            print(
                f"[FAILED_SKILL] distill={happened} class={failure_class} "
                f"endpoints={len(endpoints)} case_id={case_id_final}"
            )
            if not happened:
                print(f"[FAILED_SKILL] VETO: {vet.veto_code} - {vet.reason}")
        except SystemExit as exc:
            # three_lib_gate 三道闸 abort (MEMORY_ROOT 未设等);打印不阻断
            print(f"[FAILED_SKILL] gate abort(继续 return): {exc}")
        except Exception as exc:
            # 失败技能蒸馏失败不应阻断主流程,只 log
            print(f"[FAILED_SKILL] 蒸馏异常(继续 return): {exc}")

        # ── Telemetry: 覆盖默认值(无论 try 正常/异常, happened/artifact 均有值) ──
        result["_distill_happened"] = happened
        result["skill_name"] = getattr(artifact, "skill_name", None) if happened else None

    return result
