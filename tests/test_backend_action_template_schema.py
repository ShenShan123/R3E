from microsurgeon_flow.backend_skill_templates import distill_backend_skill_templates
from microsurgeon_flow.backend_template_preflight import (
    generate_template_actions,
    select_templates,
)


def _asap7_skill():
    return {
        "skill_name": "closed_trajectory_gcd_asap7_p580",
        "precondition": {
            "platform": "asap7",
            "design": "gcd",
            "backend_outcome": "closed",
            "failure_signature": {
                "period": 0.58,
                "initial_wns": -0.055,
                "final_wns": 0.001,
                "action_class_sequence": ["vt_faster", "vt_faster"],
            },
        },
        "validation": {"backend_metric": "closed"},
        "action_template": {
            "repair_strategy": "blue_llm_eco_vtswap",
            "key_actions": ["vt_faster", "vt_faster"],
        },
    }


def test_backend_skill_distills_signature_conditioned_action_template():
    templates = distill_backend_skill_templates([_asap7_skill()])

    assert len(templates) == 1
    template = templates[0]
    assert template["schema_version"] == 2
    assert template["skill_type"] == "backend_action_template"
    assert template["name"] == "asap7_multivt_endpoint_cone_acceleration"
    assert template["trigger"]["pdk"] == "asap7"
    assert template["trigger"]["has_multivt"] is True
    assert template["trigger"]["slack_gap_policy_ps"]["50-100"] == "reliable_target_bucket"
    assert template["trigger"]["slack_gap_policy_ps"]["100-150"].startswith("exploratory")
    assert template["binding_policy"]["candidate_region"] == "critical_path_endpoint_cone"
    assert template["action_policy"]["route"] == "vt_swap"
    assert template["action_policy"]["sequence"] == ["RVT_to_LVT", "LVT_to_SLVT"]
    assert template["fallback"]["if_no_progress"] == "classify_or_loopback"
    assert template["usage"]["route_rank_stop_only"] is True
    assert template["guard"]["sta_required"] is True


def test_action_template_routes_ranks_but_does_not_accept_without_sta():
    template = distill_backend_skill_templates([_asap7_skill()])[0]
    selected = select_templates(
        [template],
        design="heldout",
        platform="asap7",
        period=0.56,
    )
    actions, sources = generate_template_actions(
        selected,
        scoped_legal={
            "u_logic": ["NAND2xp33_ASAP7_75t_L", "NAND2xp33_ASAP7_75t_SL"],
        },
        inst_master_map={
            "u_logic": "NAND2xp33_ASAP7_75t_R",
        },
        max_actions=2,
    )

    assert len(actions) == 1
    assert actions[0].action_type == "size_cell"
    assert actions[0].params["template_name"] == "asap7_multivt_endpoint_cone_acceleration"
    assert actions[0].params["stop_policy"]["fallback"] == "classify_or_loopback"
    assert sources[0]["skill_type"] == "backend_action_template"
    assert sources[0]["stop_policy"]["stop_when"][0] == "final_wns_ge_0"


def test_route_only_action_template_does_not_emit_size_cell_action():
    template = {
        "schema_version": 2,
        "skill_type": "backend_action_template",
        "name": "single_vt_sizing_plateau_detector",
        "template_id": "nangate45:route_stop:single_vt_sizing_plateau_detector",
        "platform": "nangate45",
        "trigger": {"pdk": "nangate45"},
        "binding_policy": {
            "candidate_region": "critical_path_endpoint_cone",
            "rank_features": ["repeated_no_improve"],
        },
        "action_policy": {
            "route": "stop_or_diagnose",
            "sequence": ["classify_sizing_plateau"],
            "evaluate_after_each_action": True,
            "stop_when": ["repeated_no_improve"],
        },
        "evidence": {"support": 1},
        "fallback": {"if_no_progress": "loopback_or_report_backend_boundary"},
        "usage": {
            "route_rank_stop_only": True,
            "not_exact_replay": True,
            "sta_gate_required": True,
        },
    }

    actions, sources = generate_template_actions(
        [template],
        scoped_legal={"u_buf": ["BUF_X2"]},
        inst_master_map={"u_buf": "BUF_X1"},
        max_actions=2,
    )

    assert actions == []
    assert sources[0]["route_only"] is True
    assert sources[0]["route"] == "stop_or_diagnose"
