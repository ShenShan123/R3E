"""Runtime helpers for guarded backend template preflight."""
from __future__ import annotations

import json
from pathlib import Path

from microsurgeon_flow.backend_eco_oneshot import EcoAction
from microsurgeon_flow.backend_pattern_templates import (
    cell_family,
    cell_group,
    drive_move,
)


def load_template_file(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [t for t in data.get("templates", []) if isinstance(t, dict)]
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    return []


def load_pattern_template_file(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [p for p in data.get("patterns", []) if isinstance(p, dict)]
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    return []


def _template_platform(template: dict) -> str | None:
    return template.get("platform") or (template.get("trigger") or {}).get("pdk")


def _template_support(template: dict) -> int:
    evidence = template.get("evidence") or {}
    return int(evidence.get("support") or evidence.get("trials") or 0)


def _template_confidence(template: dict) -> str | None:
    return template.get("confidence") or template.get("evidence", {}).get("confidence")


def _search_policy_from_action_template(template: dict) -> dict:
    policy = dict(template.get("search_policy") or {})
    if policy:
        return policy
    action_policy = template.get("action_policy") or {}
    binding_policy = template.get("binding_policy") or {}
    sequence = list(action_policy.get("sequence") or [])
    rank_features = list(binding_policy.get("rank_features") or [])
    near_endpoint_first = "near_endpoint" in rank_features
    if action_policy.get("route") == "vt_swap" or any("SLVT" in str(s) for s in sequence):
        target_order = ["combinational", "buffer_or_inverter", "sequential"]
        master_choices = ["fastest_vt"]
    else:
        target_order = ["combinational", "buffer_or_inverter", "sequential"]
        master_choices = ["next_drive", "max_drive"]
    if not near_endpoint_first:
        target_order = ["buffer_or_inverter", "combinational", "sequential"]
    return {
        "target_order": target_order,
        "master_choices": master_choices,
        "max_candidate_actions": int(action_policy.get("max_candidate_actions") or 9),
    }


def _stop_policy(template: dict) -> dict:
    action_policy = template.get("action_policy") or {}
    return {
        "evaluate_after_each_action": bool(
            action_policy.get("evaluate_after_each_action", True)
        ),
        "stop_when": list(action_policy.get("stop_when") or [
            "final_wns_ge_0",
            "no_legal_candidate",
            "marginal_gain_below_threshold",
        ]),
        "fallback": (template.get("fallback") or {}).get("if_no_progress")
        or template.get("guard", {}).get("fallback")
        or "llm",
    }


def select_templates(
    templates: list[dict],
    *,
    design: str,
    platform: str,
    period: float | None,
    max_templates: int = 3,
) -> list[dict]:
    scored: list[tuple[float, dict]] = []
    for template in templates:
        if _template_platform(template) != platform:
            continue
        evidence = template.get("evidence", {})
        if _template_support(template) <= 0:
            continue
        score = 2.0
        if design in set(template.get("source_designs", [])):
            score += 1.0
        conf = _template_confidence(template)
        if conf == "high_guarded":
            score += 1.0
        elif conf in {"guarded", "guarded_observed"}:
            score += 0.5
        if template.get("skill_type") == "backend_action_template":
            score += 0.25
        pre = template.get("preconditions", {})
        pmin = pre.get("period_min")
        pmax = pre.get("period_max")
        if period is not None and pmin is not None and pmax is not None:
            if float(pmin) <= float(period) <= float(pmax):
                score += 0.5
            else:
                span = max(abs(float(period)), 1e-9)
                score += max(0.0, 0.25 - min(
                    abs(float(period) - float(pmin)),
                    abs(float(period) - float(pmax)),
                ) / span)
        scored.append((score, template))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [template for _, template in scored[:max_templates]]


def _cell_role(master: str) -> str:
    upper = master.upper()
    if upper.startswith(("DFF", "SDFF", "DF")):
        return "sequential"
    if upper.startswith(("BUF", "INV", "CLKBUF", "CLKINV")):
        return "buffer_or_inverter"
    return "combinational"


def _ordered_instances(
    inst_master_map: dict[str, str],
    target_order: list[str],
) -> list[tuple[str, str]]:
    order = {role: idx for idx, role in enumerate(target_order)}
    original_order = {inst: idx for idx, inst in enumerate(inst_master_map)}
    return sorted(
        inst_master_map.items(),
        key=lambda row: (
            order.get(_cell_role(row[1]), len(order)),
            original_order.get(row[0], len(original_order)),
        ),
    )


def _master_choices(legal: list[str], policy: list[str]) -> list[str]:
    out: list[str] = []
    for choice in policy:
        if not legal:
            continue
        if choice in {"next_drive", "first", "lowest"}:
            out.append(legal[0])
        elif choice in {"max_drive", "fastest_vt", "last", "highest"}:
            out.append(legal[-1])
    dedup: list[str] = []
    for master in out:
        if master not in dedup:
            dedup.append(master)
    return dedup


def _observation_ranked_target_order(template: dict, fallback: list[str]) -> list[str]:
    obs = template.get("action_observation_evidence") or {}
    by_role = obs.get("by_target_role") or {}
    if not by_role:
        return fallback

    def score(role: str) -> tuple[float, int]:
        labels = by_role.get(role, {})
        pos = int(labels.get("positive_improved") or 0)
        neg = int(labels.get("negative_degraded") or 0)
        neutral = int(labels.get("neutral_no_effect") or 0)
        return (float(pos) - float(neg) - 0.1 * float(neutral), pos)

    roles = list(dict.fromkeys(fallback + [str(r) for r in by_role]))
    roles.sort(key=lambda role: score(role), reverse=True)
    return roles


def generate_template_actions(
    templates: list[dict],
    scoped_legal: dict[str, list[str]],
    inst_master_map: dict[str, str],
    *,
    max_actions: int = 9,
) -> tuple[list[EcoAction], list[dict]]:
    """Generate bounded candidate actions; STA gate decides acceptance."""
    actions: list[EcoAction] = []
    sources: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for template in templates:
        action_policy = template.get("action_policy") or {}
        route = action_policy.get("route")
        if route in {"stop_or_diagnose", "loopback", "abstain"}:
            sources.append({
                "template_id": template.get("template_id"),
                "skill_type": template.get("skill_type"),
                "template_name": template.get("name"),
                "confidence": _template_confidence(template),
                "support": _template_support(template),
                "source_strategy": template.get("source_strategy"),
                "route_only": True,
                "route": route,
                "binding_policy": template.get("binding_policy"),
                "action_policy": action_policy,
                "stop_policy": _stop_policy(template),
            })
            continue
        policy = _search_policy_from_action_template(template)
        target_order = list(policy.get("target_order") or [
            "buffer_or_inverter",
            "combinational",
            "sequential",
        ])
        target_order = _observation_ranked_target_order(template, target_order)
        master_policy = list(policy.get("master_choices") or ["next_drive"])
        per_template_limit = min(
            int(policy.get("max_candidate_actions") or max_actions),
            max_actions,
        )
        made_for_template = 0
        for inst, current_master in _ordered_instances(inst_master_map, target_order):
            legal = scoped_legal.get(inst, [])
            for new_master in _master_choices(legal, master_policy):
                key = (inst, new_master)
                if key in seen:
                    continue
                seen.add(key)
                role = _cell_role(current_master)
                actions.append(EcoAction(
                    action_type="size_cell",
                    target_inst=inst,
                    params={
                        "new_master": new_master,
                        "template_id": template.get("template_id"),
                        "skill_type": template.get("skill_type"),
                        "template_name": template.get("name"),
                        "current_master": current_master,
                        "target_role": role,
                        "stop_policy": _stop_policy(template),
                    },
                ))
                sources.append({
                    "template_id": template.get("template_id"),
                    "skill_type": template.get("skill_type"),
                    "template_name": template.get("name"),
                    "confidence": _template_confidence(template),
                    "support": _template_support(template),
                    "source_strategy": template.get("source_strategy"),
                    "target_inst": inst,
                    "current_master": current_master,
                    "new_master": new_master,
                    "target_role": role,
                    "binding_policy": template.get("binding_policy"),
                    "action_policy": template.get("action_policy"),
                    "stop_policy": _stop_policy(template),
                })
                made_for_template += 1
                if len(actions) >= max_actions or made_for_template >= per_template_limit:
                    return actions, sources
    return actions, sources


def _pattern_score(pattern: dict) -> tuple[float, float]:
    evidence = pattern.get("evidence", {})
    conf = pattern.get("confidence")
    conf_bonus = 10.0 if conf == "positive_prior" else 0.0
    return (
        conf_bonus + float(evidence.get("score") or 0.0),
        float(evidence.get("mean_delta_wns") or -999.0),
    )


def _master_for_move(current_master: str, legal: list[str], move: str) -> list[str]:
    out = []
    for new_master in legal:
        if drive_move(current_master, new_master) == move:
            out.append(new_master)
    return out


def generate_pattern_actions(
    patterns: list[dict],
    scoped_legal: dict[str, list[str]],
    inst_master_map: dict[str, str],
    *,
    max_actions: int = 9,
) -> tuple[list[EcoAction], list[dict]]:
    """Generate candidates from transferable pattern priors, not exact replay."""
    actions: list[EcoAction] = []
    sources: list[dict] = []
    seen: set[tuple[str, str]] = set()
    positive = [
        p for p in patterns
        if p.get("confidence") in {"positive_prior", "mixed_prior"}
        and (p.get("evidence", {}).get("positive") or 0) > 0
        and float(p.get("evidence", {}).get("score") or 0.0) > 0.0
    ]
    positive.sort(key=_pattern_score, reverse=True)
    for pattern in positive:
        selector = pattern.get("selector", {})
        target_role = selector.get("target_role")
        target_group = selector.get("cell_group")
        move = selector.get("drive_move")
        for inst, current_master in inst_master_map.items():
            role = _cell_role(current_master)
            family = cell_family(current_master)
            group = cell_group(family)
            if target_role and role != target_role:
                continue
            if target_group and group != target_group:
                continue
            for new_master in _master_for_move(current_master, scoped_legal.get(inst, []), str(move)):
                key = (inst, new_master)
                if key in seen:
                    continue
                seen.add(key)
                actions.append(EcoAction(
                    action_type="size_cell",
                    target_inst=inst,
                    params={
                        "new_master": new_master,
                        "pattern_id": pattern.get("pattern_id"),
                        "source_template_id": pattern.get("source_template_id"),
                        "current_master": current_master,
                        "target_role": role,
                        "cell_family": family,
                        "cell_group": group,
                        "drive_move": move,
                    },
                ))
                sources.append({
                    "pattern_id": pattern.get("pattern_id"),
                    "confidence": pattern.get("confidence"),
                    "support": pattern.get("evidence", {}).get("support"),
                    "score": pattern.get("evidence", {}).get("score"),
                    "target_inst": inst,
                    "current_master": current_master,
                    "new_master": new_master,
                    "target_role": role,
                    "cell_family": family,
                    "cell_group": group,
                    "drive_move": move,
                })
                if len(actions) >= max_actions:
                    return actions, sources
    return actions, sources
