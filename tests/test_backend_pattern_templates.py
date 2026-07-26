from microsurgeon_flow.backend_pattern_templates import aggregate_pattern_templates
from microsurgeon_flow.backend_template_preflight import generate_pattern_actions


def _record(*, role, old, new, delta, label):
    return {
        "record_type": "backend_template_action_observation",
        "case": {"platform": "nangate45", "name": "case0"},
        "template": {
            "template_id": "nangate45:strength_upsize_critical_path:orfs_builtin_repair_timing"
        },
        "action": {
            "action_type": "size_cell",
            "target_role": role,
            "current_master": old,
            "new_master": new,
        },
        "sta": {"delta_wns": delta, "label": label},
    }


def test_aggregate_records_to_transferable_patterns():
    patterns = aggregate_pattern_templates([
        _record(role="combinational", old="NAND2_X2", new="NAND2_X4",
                delta=0.01, label="positive_improved"),
        _record(role="buffer_or_inverter", old="INV_X4", new="INV_X32",
                delta=-0.05, label="negative_degraded"),
    ])

    by_conf = {p["confidence"]: p for p in patterns}
    assert by_conf["positive_prior"]["selector"]["cell_group"] == "basic_logic"
    assert by_conf["negative_blacklist"]["selector"]["cell_group"] == "buffer_or_inverter"


def test_generate_pattern_actions_uses_positive_prior_not_negative_buffer():
    patterns = aggregate_pattern_templates([
        _record(role="combinational", old="NAND2_X2", new="NAND2_X4",
                delta=0.01, label="positive_improved"),
        _record(role="buffer_or_inverter", old="INV_X4", new="INV_X32",
                delta=-0.05, label="negative_degraded"),
    ])
    actions, sources = generate_pattern_actions(
        patterns,
        {
            "u_logic": ["NAND2_X4"],
            "u_inv": ["INV_X8", "INV_X32"],
        },
        {
            "u_logic": "NAND2_X2",
            "u_inv": "INV_X4",
        },
        max_actions=3,
    )

    assert [a.target_inst for a in actions] == ["u_logic"]
    assert actions[0].params["new_master"] == "NAND2_X4"
    assert sources[0]["pattern_id"].startswith("nangate45:combinational:basic_logic")
