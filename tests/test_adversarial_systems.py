from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RED_ROOT = ROOT / "experiments" / "red_team_system"
BLUE_ROOT = ROOT / "experiments" / "blue_team_system"
for path in (RED_ROOT, BLUE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from adversarial_design_config import (  # noqa: E402
    DesignConfig,
    select_final_netlist,
    validate_design_inputs,
)
from memory_distillation import MemoryDistillation  # noqa: E402
from red_agent import PoisonAction, PoisonPlan, RedAgent  # noqa: E402
from reward_calculator import RewardCalculator  # noqa: E402


def _action(target: str, action_type: str = "modify_rtl") -> PoisonAction:
    return PoisonAction(
        target_file=target,
        action_type=action_type,
        level=1,
        location={"line_start": 1, "line_end": 1},
        payload="module changed; endmodule",
        rationale="test",
        stealth_score=0.5,
    )


def test_red_agent_materializes_only_inside_sandbox(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "top.v"
    source.write_text("module top; endmodule\n", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    agent = RedAgent(
        api_key="x",
        project_root=str(project),
        memory_dir=str(tmp_path / "memory"),
        sandbox_work_root=str(sandbox),
    )
    plan = PoisonPlan([_action("top.v")], 1, "test", "test")

    success, modified = agent.execute_poison_plan(plan)

    assert success and modified == ["top.v"]
    assert source.read_text(encoding="utf-8") == "module top; endmodule\n"
    assert (sandbox / "materialized" / "top.v").read_text(encoding="utf-8") == (
        "module changed; endmodule\n"
    )


@pytest.mark.parametrize("target", ["../secret.v", "/tmp/secret.v"])
def test_red_agent_rejects_path_escape(tmp_path: Path, target: str):
    project = tmp_path / "project"
    project.mkdir()
    agent = RedAgent(
        api_key="x",
        project_root=str(project),
        memory_dir=str(tmp_path / "memory"),
        sandbox_work_root=str(tmp_path / "sandbox"),
    )
    plan = PoisonPlan([_action(target)], 1, "test", "test")

    success, modified = agent.execute_poison_plan(plan)

    assert not success and modified == []


def test_unverified_red_outcome_is_pending_not_success(tmp_path: Path):
    reward = RewardCalculator().calculate_total_reward(
        frontend_passed=True,
        repair_success=False,
        repair_observed=False,
        poison_level=1,
    )
    assert reward.repair_cost == 0.0
    assert reward.total_reward == 0.0

    memory = MemoryDistillation(str(tmp_path / "memory"))
    memory_id = memory.update_memory(
        actions=[{"target_file": "top.v"}],
        strategy="test",
        poison_level=1,
        target_files=["top.v"],
        is_frontend_pass=True,
        reward=reward.total_reward,
        reward_breakdown=reward.breakdown,
        backend_evaluated=False,
    )
    assert memory.load_memory(memory_id).category == "pending"
    assert memory.get_top_successes() == []


def test_identical_poison_sdc_is_rejected(tmp_path: Path):
    rtl = tmp_path / "top.v"
    canonical = tmp_path / "top.sdc"
    rtl.write_text("module top; endmodule\n", encoding="utf-8")
    canonical.write_text("create_clock -period 1 [get_ports clk]\n", encoding="utf-8")
    config = DesignConfig("top", "top", rtl, canonical, 1.0, "clk", None, None)

    with pytest.raises(RuntimeError, match="byte-identical"):
        validate_design_inputs(config, canonical)


def test_empty_final_netlist_uses_file_fallback(tmp_path: Path):
    fallback = tmp_path / "fallback.v"
    fallback.write_text("module top; endmodule\n", encoding="utf-8")
    assert select_final_netlist({"final_netlist": ""}, fallback) == fallback

