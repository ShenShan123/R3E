"""Build guarded backend ECO templates from the backend skill memory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "r3e"))

from microsurgeon_flow.backend_skill_templates import (  # noqa: E402
    build_template_file,
    write_templates,
)


ROOT = REPO_ROOT
DEFAULT_SKILL_LIB = ROOT / ".micro_surgeon_memory/backend_lib/skill_index.jsonl"
DEFAULT_OUT = ROOT / ".iso_semrepair/backend_skill_templates.json"
DEFAULT_TRAINING_RECORDS = ROOT / ".iso_semrepair/backend_template_training_records.jsonl"
DEFAULT_LOOPBACK_LIB = ROOT / ".micro_surgeon_memory/loopback_lib/skill_index.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _has_template(templates: list[dict], name: str) -> bool:
    return any(t.get("name") == name for t in templates)


def _nangate45_plateau_template(templates: list[dict]) -> dict:
    sizing = next(
        (
            t for t in templates
            if t.get("platform") == "nangate45"
            and t.get("template_kind") == "strength_upsize_critical_path"
        ),
        {},
    )
    evidence = sizing.get("evidence") or {}
    support = int(evidence.get("support") or evidence.get("trials") or 0)
    closed = int(evidence.get("closed") or 0)
    routed_unclosed = int(evidence.get("routed_unclosed") or 0)
    return {
        "schema_version": 2,
        "skill_type": "backend_action_template",
        "name": "single_vt_sizing_plateau_detector",
        "template_id": "nangate45:route_stop:single_vt_sizing_plateau_detector",
        "platform": "nangate45",
        "template_kind": "route_stop",
        "action_type": "route_decision",
        "source_strategy": "orfs_builtin_repair_timing_boundary_evidence",
        "source_designs": sorted(set(sizing.get("source_designs") or ["gcd"])),
        "trigger": {
            "pdk": "nangate45",
            "has_multivt": False,
            "path_type": ["reg2reg", "reg2out"],
            "slack_gap_ps": ["0-100"],
            "dominant_delay": ["cell", "mixed"],
            "legal_faster_vt_count_min": 0,
            "failure_signature": [
                "single_vt",
                "repeated_no_improve",
                "no_legal_faster_vt",
            ],
        },
        "binding_policy": {
            "candidate_region": "critical_path_endpoint_cone",
            "rank_features": [
                "single_vt",
                "no_legal_vt_headroom",
                "repeated_no_improve",
                "marginal_gain_below_threshold",
            ],
        },
        "action_policy": {
            "route": "stop_or_diagnose",
            "sequence": ["classify_sizing_plateau"],
            "batch_size": 0,
            "evaluate_after_each_action": True,
            "stop_when": [
                "repeated_no_improve",
                "no_legal_candidate",
                "marginal_gain_below_threshold",
            ],
        },
        "preconditions": {
            "platform": "nangate45",
            "has_multivt": False,
        },
        "search_policy": {
            "target_order": [],
            "master_choices": [],
            "max_candidate_actions": 0,
        },
        "evidence": {
            "support": support,
            "trials": support,
            "closed": closed,
            "routed_unclosed": routed_unclosed,
            "improved": 0,
            "mean_delta_wns_ps": evidence.get("mean_delta_wns_ps"),
            "counterexamples": [],
            "source_template_id": sizing.get("template_id"),
        },
        "confidence": "guarded_observed" if support else "weak_guarded",
        "guard": {
            "sta_required": True,
            "commit_rule": "no_backend_commit_without_sta_gate",
            "fallback": "loopback_or_report_backend_boundary",
        },
        "fallback": {
            "if_no_progress": "loopback_or_report_backend_boundary",
        },
        "usage": {
            "route_rank_stop_only": True,
            "not_exact_replay": True,
            "sta_gate_required": True,
        },
    }


def _structural_loopback_template(loopback_rows: list[dict]) -> dict:
    reclosed = [
        row for row in loopback_rows
        if row.get("validation", {}).get("loopback_metric") == "reclosed"
    ]
    source_designs = sorted({
        str(row.get("case_id", "")).split("_", 1)[0]
        for row in reclosed
        if row.get("case_id")
    })
    return {
        "schema_version": 2,
        "skill_type": "backend_action_template",
        "name": "structural_bottleneck_loopback_route",
        "template_id": "generic:route_stop:structural_bottleneck_loopback_route",
        "platform": "generic",
        "template_kind": "route_stop",
        "action_type": "route_decision",
        "source_strategy": "loopback_structural_bottleneck_to_frontend",
        "source_designs": source_designs,
        "trigger": {
            "pdk": "any",
            "has_multivt": None,
            "path_type": ["reg2reg", "reg2out"],
            "slack_gap_ps": ["any_negative"],
            "dominant_delay": ["structural", "mixed"],
            "legal_faster_vt_count_min": 0,
            "failure_signature": [
                "backend_exhausted",
                "structural_bottleneck",
                "frontend_restructure_available",
            ],
        },
        "binding_policy": {
            "candidate_region": "rtl_module_or_endpoint_cone",
            "rank_features": [
                "module_prefix_from_endpoint",
                "structural_depth",
                "repeated_backend_no_progress",
                "frontend_edit_scope_available",
            ],
        },
        "action_policy": {
            "route": "loopback",
            "sequence": ["frontend_retime_or_restructure"],
            "batch_size": 0,
            "evaluate_after_each_action": True,
            "stop_when": [
                "route_to_frontend_loopback",
                "formal_gate_failed",
                "loopback_reopen_unclosed",
            ],
        },
        "preconditions": {
            "platform": "any",
            "failure_class": "structural_bottleneck",
        },
        "search_policy": {
            "target_order": [],
            "master_choices": [],
            "max_candidate_actions": 0,
        },
        "evidence": {
            "support": len(reclosed),
            "trials": len(loopback_rows),
            "closed": len(reclosed),
            "improved": len(reclosed),
            "mean_delta_wns_ps": None,
            "counterexamples": ["formal_pass_but_reopen_unclosed"],
            "source_skill_names": sorted({
                row.get("skill_name", "") for row in reclosed if row.get("skill_name")
            }),
        },
        "confidence": "guarded" if reclosed else "weak_guarded",
        "guard": {
            "sta_required": True,
            "formal_required": True,
            "commit_rule": "formal_then_reclose_gate",
            "fallback": "no_promotion_if_formal_or_reclose_fails",
        },
        "fallback": {
            "if_no_progress": "no_promotion_if_formal_fails",
        },
        "usage": {
            "route_rank_stop_only": True,
            "not_exact_replay": True,
            "sta_gate_required": True,
        },
    }


def append_route_stop_templates(
    templates: list[dict],
    *,
    loopback_lib: Path,
) -> list[dict]:
    out = list(templates)
    if not _has_template(out, "single_vt_sizing_plateau_detector"):
        out.append(_nangate45_plateau_template(out))
    if not _has_template(out, "structural_bottleneck_loopback_route"):
        out.append(_structural_loopback_template(_load_jsonl(loopback_lib)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-lib", default=str(DEFAULT_SKILL_LIB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument(
        "--training-records",
        default=str(DEFAULT_TRAINING_RECORDS),
        help="Optional action-level observation JSONL to attach as template evidence.",
    )
    ap.add_argument("--loopback-lib", default=str(DEFAULT_LOOPBACK_LIB))
    ap.add_argument(
        "--no-route-stop-templates",
        action="store_true",
        help="Do not append experimental route/stop policy templates.",
    )
    args = ap.parse_args()

    templates = build_template_file(
        args.skill_lib,
        args.out,
        training_records=args.training_records,
    )
    if not args.no_route_stop_templates:
        templates = append_route_stop_templates(
            templates,
            loopback_lib=Path(args.loopback_lib),
        )
        write_templates(templates, args.out)
    summary = {
        "skill_lib": args.skill_lib,
        "out": args.out,
        "template_count": len(templates),
        "templates": [
            {
                "template_id": t.get("template_id"),
                "confidence": t.get("confidence"),
                "evidence": t.get("evidence"),
                "action_observation_evidence": t.get("action_observation_evidence"),
                "search_policy": t.get("search_policy"),
            }
            for t in templates
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
