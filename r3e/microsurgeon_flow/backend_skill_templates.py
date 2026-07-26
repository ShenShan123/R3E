"""Distill backend skill memories into guarded ECO action templates.

The backend skill library contains mixed records: true trajectory skills with
action-class sequences, plus older deterministic ORFS records that only say a
strategy worked or partially worked.  This module normalizes those memories
into small executable template descriptors.  Templates are still guarded: they
select candidate actions, then STA decides whether an action is committed.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

_POSITIVE_STATES = {"closed", "routed_unclosed"}
_ALL_STATES = _POSITIVE_STATES | {"failed"}
_STATE_PARSE_ORDER = ("routed_unclosed", "closed", "failed")


def load_backend_skills(lib_path: str | Path) -> list[dict]:
    path = Path(lib_path)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _metric_of(skill: dict) -> str | None:
    raw = (
        skill.get("validation", {}).get("backend_metric")
        or skill.get("precondition", {}).get("backend_outcome")
        or skill.get("action_template", {}).get("tristate")
    )
    if raw in _ALL_STATES:
        return raw
    text = str(raw or "")
    for state in _STATE_PARSE_ORDER:
        if state in text:
            return state
    return None


def _period_of(skill: dict) -> float | None:
    pc = skill.get("precondition", {})
    fs = pc.get("failure_signature", {})
    val = fs.get("period")
    if val is None:
        poison = str(pc.get("poison_param", ""))
        if poison.startswith("clk_period="):
            val = poison.split("=", 1)[1]
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _wns_values(skill: dict) -> tuple[float | None, float | None]:
    fs = skill.get("precondition", {}).get("failure_signature", {})
    initial = fs.get("initial_wns")
    final = fs.get("final_wns")
    if initial is None:
        initial = skill.get("action_template", {}).get("setup_wns")
    if final is None:
        final = fs.get("final_wns")
    try:
        initial_f = float(initial) if initial is not None else None
    except (TypeError, ValueError):
        initial_f = None
    try:
        final_f = float(final) if final is not None else None
    except (TypeError, ValueError):
        final_f = None
    return initial_f, final_f


def _action_classes(skill: dict) -> list[str]:
    fs = skill.get("precondition", {}).get("failure_signature", {})
    at = skill.get("action_template", {})
    seq = fs.get("action_class_sequence") or at.get("key_actions") or []
    return [str(x) for x in seq if x]


def _template_kind(skill: dict) -> str | None:
    platform = skill.get("precondition", {}).get("platform")
    strategy = str(skill.get("action_template", {}).get("repair_strategy", ""))
    classes = set(_action_classes(skill))
    if "vt_faster" in classes or "vtswap" in strategy:
        return "vt_swap_critical_path"
    if platform == "nangate45" and (
        "orfs_builtin_repair_timing" in strategy
        or {"upsize", "size_cell", "resize_chain"} & classes
        or "size" in strategy
        or "repair_timing" in strategy
    ):
        return "strength_upsize_critical_path"
    return None


def _confidence(*, support: int, closed: int, deltas: list[float]) -> str:
    if support <= 0:
        return "disabled"
    if closed and deltas and min(deltas) >= 0.0:
        return "high_guarded"
    if closed:
        return "guarded"
    return "weak_guarded"


def _template_name(platform: str, kind: str) -> str:
    if platform == "asap7" and kind == "vt_swap_critical_path":
        return "asap7_multivt_endpoint_cone_acceleration"
    if kind == "strength_upsize_critical_path":
        return f"{platform}_critical_path_strength_upsize"
    return f"{platform}_{kind}"


def _timing_value_to_ps(value: float) -> float:
    """Normalize WNS/delta values to ps.

    Older Nangate probe records store WNS in ns, while ASAP7 trajectory records
    store ps-like values.  Treat magnitudes above 10 as already-ps.
    """
    return value if abs(value) > 10.0 else value * 1000.0


def _trigger_for(platform: str, kind: str, initials: list[float]) -> dict:
    gaps_ps = [max(0.0, -_timing_value_to_ps(v)) for v in initials if v < 0.0]
    if gaps_ps:
        gap_min = int(min(gaps_ps) // 50 * 50)
        gap_max = int((max(gaps_ps) + 49.999) // 50 * 50)
        observed_gap = [f"{gap_min}-{gap_max}"]
    elif kind == "vt_swap_critical_path":
        observed_gap = ["unknown"]
    else:
        observed_gap = ["any_negative"]
    trigger = {
        "pdk": platform,
        "has_multivt": platform == "asap7",
        "path_type": ["reg2reg", "reg2out"],
        "slack_gap_ps": observed_gap,
        "dominant_delay": ["cell", "mixed"],
        "legal_faster_vt_count_min": 1 if kind == "vt_swap_critical_path" else 0,
    }
    if platform == "asap7" and kind == "vt_swap_critical_path":
        trigger["slack_gap_policy_ps"] = {
            "0-50": "easy_positive_try_vt_swap",
            "50-100": "reliable_target_bucket",
            "100-150": "exploratory_continue_if_marginal_gain_positive",
            ">150": "likely_abstain_or_loopback_unless_evidence_says_otherwise",
        }
        trigger["observed_reliable_bucket_ps"] = observed_gap
    return trigger


def _binding_policy_for(kind: str) -> dict:
    if kind == "vt_swap_critical_path":
        rank_features = [
            "cell_delay_contribution",
            "near_endpoint",
            "legal_vt_headroom",
            "non_sequential",
            "not_clock_cell",
        ]
    else:
        rank_features = [
            "cell_delay_contribution",
            "near_endpoint",
            "legal_drive_headroom",
            "non_sequential",
            "not_clock_cell",
        ]
    return {
        "candidate_region": "critical_path_endpoint_cone",
        "rank_features": rank_features,
    }


def _action_policy_for(kind: str) -> dict:
    if kind == "vt_swap_critical_path":
        sequence = ["RVT_to_LVT", "LVT_to_SLVT"]
        route = "vt_swap"
    else:
        sequence = ["size_cell_next_drive", "size_cell_max_drive"]
        route = "sizing"
    return {
        "route": route,
        "sequence": sequence,
        "batch_size": 1,
        "evaluate_after_each_action": True,
        "stop_when": [
            "final_wns_ge_0",
            "no_legal_candidate",
            "marginal_gain_below_threshold",
        ],
    }


def _evidence_v2(
    *,
    support: int,
    closed: int,
    routed: int,
    deltas: list[float],
) -> dict:
    improved = sum(1 for delta in deltas if delta > 0.0)
    deltas_ps = [_timing_value_to_ps(delta) for delta in deltas]
    return {
        "trials": support,
        "closed": closed,
        "improved": max(improved, closed + routed if deltas else 0),
        "mean_delta_wns_ps": mean(deltas_ps) if deltas_ps else None,
        "min_delta_wns_ps": min(deltas_ps) if deltas_ps else None,
        "max_delta_wns_ps": max(deltas_ps) if deltas_ps else None,
        "counterexamples": [],
    }


def distill_backend_skill_templates(skills: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for skill in skills:
        metric = _metric_of(skill)
        if metric not in _POSITIVE_STATES:
            continue
        kind = _template_kind(skill)
        if not kind:
            continue
        pc = skill.get("precondition", {})
        platform = str(pc.get("platform", "unknown"))
        strategy = str(skill.get("action_template", {}).get("repair_strategy", "unknown"))
        buckets.setdefault((platform, kind, strategy), []).append(skill)

    templates: list[dict] = []
    for (platform, kind, strategy), items in sorted(buckets.items()):
        periods = [p for p in (_period_of(s) for s in items) if p is not None]
        initials: list[float] = []
        deltas: list[float] = []
        closed = 0
        routed = 0
        source_designs: set[str] = set()
        source_skill_names: list[str] = []
        for skill in items:
            metric = _metric_of(skill)
            if metric == "closed":
                closed += 1
            elif metric == "routed_unclosed":
                routed += 1
            pc = skill.get("precondition", {})
            if pc.get("design"):
                source_designs.add(str(pc["design"]))
            if skill.get("skill_name"):
                source_skill_names.append(str(skill["skill_name"]))
            initial, final = _wns_values(skill)
            if initial is not None:
                initials.append(initial)
            if initial is not None and final is not None:
                deltas.append(final - initial)

        support = len(items)
        template_id = f"{platform}:{kind}:{strategy}"
        if kind == "strength_upsize_critical_path":
            search_policy = {
                "target_order": ["buffer_or_inverter", "combinational", "sequential"],
                "master_choices": ["next_drive", "max_drive"],
                "max_candidate_actions": 9,
            }
        else:
            search_policy = {
                "target_order": ["sequential", "combinational", "buffer_or_inverter"],
                "master_choices": ["fastest_vt"],
                "max_candidate_actions": 9,
            }
        templates.append({
            "schema_version": 2,
            "skill_type": "backend_action_template",
            "name": _template_name(platform, kind),
            "template_id": template_id,
            "platform": platform,
            "template_kind": kind,
            "action_type": "size_cell",
            "source_strategy": strategy,
            "source_designs": sorted(source_designs),
            "trigger": _trigger_for(platform, kind, initials),
            "binding_policy": _binding_policy_for(kind),
            "action_policy": _action_policy_for(kind),
            "preconditions": {
                "platform": platform,
                "period_min": min(periods) if periods else None,
                "period_max": max(periods) if periods else None,
                "initial_wns_min": min(initials) if initials else None,
                "initial_wns_max": max(initials) if initials else None,
            },
            "search_policy": search_policy,
            "evidence": {
                "support": support,
                "trials": support,
                "closed": closed,
                "routed_unclosed": routed,
                "improved": sum(1 for delta in deltas if delta > 0.0),
                "mean_wns_delta": mean(deltas) if deltas else None,
                "mean_delta_wns_ps": (
                    mean([_timing_value_to_ps(delta) for delta in deltas])
                    if deltas else None
                ),
                "min_wns_delta": min(deltas) if deltas else None,
                "source_skill_names": sorted(set(source_skill_names)),
                "action_template_stats": _evidence_v2(
                    support=support,
                    closed=closed,
                    routed=routed,
                    deltas=deltas,
                ),
            },
            "confidence": _confidence(support=support, closed=closed, deltas=deltas),
            "guard": {
                "sta_required": True,
                "commit_rule": "new_wns > old_wns",
                "fallback": "llm",
            },
            "fallback": {
                "if_no_progress": "classify_or_loopback",
            },
            "usage": {
                "route_rank_stop_only": True,
                "not_exact_replay": True,
                "sta_gate_required": True,
            },
        })
    return templates


def write_templates(templates: list[dict], out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "template_count": len(templates),
        "templates": templates,
    }, indent=2, ensure_ascii=False))


def load_template_training_records(path: str | Path | None) -> list[dict]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    records: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("record_type") == "backend_template_action_observation":
            records.append(record)
    return records


def attach_action_observations(
    templates: list[dict],
    records: list[dict],
) -> list[dict]:
    """Attach high-information action observations to matching templates.

    The observations do not promote a template to unconditional execution.  They
    are used as ranking/blacklist evidence for future bounded candidate search.
    """
    by_template: dict[str, list[dict]] = {}
    for record in records:
        tid = record.get("template", {}).get("template_id")
        if tid:
            by_template.setdefault(str(tid), []).append(record)

    for template in templates:
        tid = str(template.get("template_id"))
        items = by_template.get(tid, [])
        if not items:
            continue
        labels: dict[str, int] = {}
        role_labels: dict[str, dict[str, int]] = {}
        positive_patterns: list[dict] = []
        negative_patterns: list[dict] = []
        selected_positive = 0
        for record in items:
            label = str(record.get("sta", {}).get("label", "unknown"))
            labels[label] = labels.get(label, 0) + 1
            action = record.get("action", {})
            role = str(action.get("target_role", "unknown"))
            role_labels.setdefault(role, {})
            role_labels[role][label] = role_labels[role].get(label, 0) + 1
            delta = record.get("sta", {}).get("delta_wns")
            pattern = {
                "case": record.get("case", {}).get("name"),
                "target_role": role,
                "current_master": action.get("current_master"),
                "new_master": action.get("new_master"),
                "delta_wns": delta,
            }
            if label == "positive_improved":
                positive_patterns.append(pattern)
            elif label == "negative_degraded":
                negative_patterns.append(pattern)
            if record.get("sta", {}).get("selected_by_gate"):
                selected_positive += 1

        positive_patterns.sort(key=lambda x: x.get("delta_wns") or 0.0, reverse=True)
        negative_patterns.sort(key=lambda x: x.get("delta_wns") or 0.0)
        template["action_observation_evidence"] = {
            "record_count": len(items),
            "by_label": labels,
            "by_target_role": role_labels,
            "selected_positive_records": selected_positive,
            "top_positive_patterns": positive_patterns[:5],
            "top_negative_patterns": negative_patterns[:5],
        }
        if labels.get("positive_improved", 0) and template.get("confidence") == "guarded":
            template["confidence"] = "guarded_observed"
    return templates


def build_template_file(
    skill_lib: str | Path,
    out_path: str | Path,
    *,
    training_records: str | Path | None = None,
) -> list[dict]:
    templates = distill_backend_skill_templates(load_backend_skills(skill_lib))
    templates = attach_action_observations(
        templates,
        load_template_training_records(training_records),
    )
    write_templates(templates, out_path)
    return templates
