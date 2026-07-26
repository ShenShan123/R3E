"""
§3.6.2 failed skill schema 单测.

数据驱动: B3 的真实 sizing_plateau 实例 (gcd 0.46, WNS -0.0596→-0.0661) 做
首个测试样本。覆盖:
  1. classify_sizing_failure 路径A 分类正确
  2. build_failed_skill_payload 六关红线全过 (vet_skill approved=True)
  3. 召回出口 filter: is_failed_skill=true 不出现在 query_skill 结果
  4. distill_skill 落 jsonl 后 schema 完整 (REQUIRED 5 字段齐)

纯逻辑, 无 ORFS 依赖, 无网络。隔离 mem_root, 不动真库。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from microsurgeon_flow.failed_skill_builder import (
    build_failed_skill_payload,
    classify_sizing_failure,
    derive_strategy_label,
)


# ── B3 真实数据 (§8.2 Step4 B 第三刀, 2026-06-09 gcd 0.46) ────────────────
B3_WNS_TRAJECTORY = [-0.0596, -0.0612, -0.0695, -0.0661]
B3_STRATEGY_TRIED = [
    "sizing_up_DFF_X4",
    "sizing_up_NAND2_X4",
    "sizing_up_AND2_X4",
    "sizing_up_BUF_X4",
]
B3_ROUNDS = 3   # §4.3 agent 级回合 (4 提案分入 3 回合, 第3回合内多策略)


# ─────────────────────────────────────────────────────────────────────────
#  1. failure_class 分类
# ─────────────────────────────────────────────────────────────────────────

def test_classify_sizing_plateau_from_b3():
    """B3 实测: 3 回合 4 种 sizing 策略均 revert (WNS 恶化) → sizing_plateau"""
    fc = classify_sizing_failure(
        wns_trajectory=B3_WNS_TRAJECTORY,
        rounds=B3_ROUNDS,
        distinct_strategies=4,
    )
    assert fc == "sizing_plateau"


def test_classify_tool_no_effect_zero_span():
    """WNS 几乎零波动 → tool_no_effect"""
    fc = classify_sizing_failure(
        wns_trajectory=[-0.0600, -0.0600, -0.06001],
        rounds=2,
        distinct_strategies=2,
    )
    assert fc == "tool_no_effect"


def test_classify_other_when_insufficient_rounds():
    """rounds<3 且 span>1e-4 → 不归 plateau, 走 other"""
    fc = classify_sizing_failure(
        wns_trajectory=[-0.06, -0.07],
        rounds=1,
        distinct_strategies=1,
    )
    assert fc == "other"


def test_classify_nonmonotonic_not_plateau():
    """
    §3.6.3 improved 判据回归门 (best-so-far 修复后必须通过):
    非单调轨迹 [-0.06, -0.02, -0.08] —— 第2轮 -0.02 已证明工具够得着 headroom
    (best-so-far > baseline)。旧首尾判据 (final -0.08 < baseline -0.06) 会误判
    sizing_plateau; 改 best-so-far 后必须落 other (回归/不稳定, 非 sizing 耗尽)。
    span=0.06≫1e-4 故亦非 tool_no_effect。
    """
    fc = classify_sizing_failure(
        wns_trajectory=[-0.06, -0.02, -0.08],
        rounds=3,
        distinct_strategies=3,
    )
    assert fc != "sizing_plateau", f"非单调中途改善不应判 plateau, 得 {fc}"
    assert fc == "other", f"预期 other (有 headroom 但回归), 得 {fc}"


def test_classify_gcd_046_b3_plateau():
    """
    §4.3 改写后硬验收门: gcd 0.46 真实轨迹重放。
    rounds=3, WNS 无改善 → sizing_plateau (判 other 即补丁不合格)。
    录得轨迹取自 artifacts/failed_skill_b3_20260609T235026/，非 LLM 重放。
    """
    fc = classify_sizing_failure(
        wns_trajectory=[-0.0596, -0.071, -0.078, -0.077],
        rounds=3,
        distinct_strategies=99,  # 任意值, 不再参与路由
    )
    assert fc == "sizing_plateau", f"B patch 不合格: 预期 sizing_plateau, 得 {fc}"


def test_classify_gcd_046_distinct_one_still_plateau():
    """对偶门: 单原语真实形态 distinct=1, 同轨迹仍判 plateau。
    与 =99 一高一低夹逼, 证明 distinct 完全不参与路由。"""
    fc = classify_sizing_failure(
        wns_trajectory=[-0.0596, -0.071, -0.078, -0.077],
        rounds=3,
        distinct_strategies=1,
    )
    assert fc == "sizing_plateau", f"distinct=1 应判 plateau, 得 {fc}"


# ─────────────────────────────────────────────────────────────────────────
#  1b. derive_strategy_label 机械派生 (A-2)
# ─────────────────────────────────────────────────────────────────────────

def test_derive_label_no_action():
    """空 actions → no_action"""
    assert derive_strategy_label({}) == "no_action"
    assert derive_strategy_label({"actions": []}) == "no_action"
    assert derive_strategy_label({"actions": None}) == "no_action"


def test_derive_label_single_type():
    """单 type → size_cell_n1"""
    label = derive_strategy_label({"actions": [
        {"action_type": "size_cell", "target_inst": "u1", "to_master": "X2"},
    ]})
    assert label == "size_cell_n1"


def test_derive_label_multi_type_sorted():
    """多 type 排序 → resize_chain+size_cell_n3"""
    label = derive_strategy_label({"actions": [
        {"action_type": "size_cell", "target_inst": "u1", "to_master": "X2"},
        {"action_type": "resize_chain", "target_inst": "u2", "to_master": "X4"},
        {"action_type": "size_cell", "target_inst": "u3", "to_master": "X3"},
    ]})
    assert label == "resize_chain+size_cell_n3"


# ─────────────────────────────────────────────────────────────────────────
#  2. 六关红线: vet_skill 直接吃 payload 必须 approved=True
# ─────────────────────────────────────────────────────────────────────────

def _make_b3_payload():
    return build_failed_skill_payload(
        case_id="gcd_gcd_p046_llm_eco_20260609T000000Z",
        design="gcd",
        platform="nangate45",
        variant="base",
        poison_param="clk_period=0.46",
        period=0.46,
        failure_class="sizing_plateau",
        wns_trajectory=B3_WNS_TRAJECTORY,
        strategy_tried=B3_STRATEGY_TRIED,
        rounds=B3_ROUNDS,
        violating_endpoints=[],  # B3 当时未采 → 首例空数组, schema 结构完整
    )


def test_payload_passes_all_six_vetting_checks(tmp_path):
    """B3 payload 必须过 vet_skill 六关 (schema/destructive/absurd/hollow/broad/cheat)"""
    from memory_integrity_guard import SkillVetting

    guard = SkillVetting(domain="backend", audit_log=tmp_path / "audit.jsonl")
    payload = _make_b3_payload()
    raw = {
        "skill_name": payload["skill_name"],
        "precondition": payload["precondition"],
        "action_template": payload["action_template"],
        "validation": payload["validation"],
        "rollback_condition": payload["rollback_condition"],
    }
    result = guard.vet_skill(raw)
    assert result.approved, (
        f"vet_skill 拒了 failed skill: veto={result.veto_code} reason={result.reason}"
    )
    assert "hollow_validation" not in result.detected_patterns
    assert "broad_scope_veto" not in result.detected_patterns
    assert "destructive_action" not in result.detected_patterns


def test_payload_no_forbidden_keys_anywhere():
    """硬约束: 绝不出现 wns_delta / wns_improvement_target / full_pass 键
    (它们撞 HOLLOW_VALIDATION_PATTERNS / BROAD_SCOPE_PATTERNS)
    """
    import json
    payload = _make_b3_payload()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert '"wns_delta"' not in serialized
    assert '"wns_improvement_target"' not in serialized
    assert '"full_pass"' not in serialized


def test_payload_scope_bounded():
    """allowed_edit_scope 非空、非 * 、非系统路径"""
    payload = _make_b3_payload()
    scope = payload["action_template"]["allowed_edit_scope"]
    assert scope == ["openroad_eco:gcd"]
    assert scope != []
    assert scope != ["*"]


def test_payload_backend_metric_is_failed():
    """validation.backend_metric='failed' (非 hollow 直接串黑名单)"""
    payload = _make_b3_payload()
    assert payload["validation"]["backend_metric"] == "failed"


# ─────────────────────────────────────────────────────────────────────────
#  3. 召回出口 filter: failed skill 不被 query_skill 返回
# ─────────────────────────────────────────────────────────────────────────

def test_failed_skill_excluded_from_query(tmp_path, monkeypatch):
    """failed skill distill 后, query_skill 不应返回它"""
    monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_REAL_MEMORY_WRITE", "0")  # 隔离根, 不动真库

    from memory_manager import SkillMemoryManager
    from memory_integrity_guard import SkillVetting

    sublib = tmp_path / "backend_lib"
    sublib.mkdir()
    mgr = SkillMemoryManager(memory_root=sublib, _skip_integrity_check=True)
    guard = SkillVetting(domain="backend", audit_log=sublib / "audit.jsonl")

    payload = _make_b3_payload()
    happened, vet, artifact = guard.vet_distill(mgr, **payload)
    assert happened, f"vet_distill 拒了: {vet.reason}"
    assert artifact is not None

    # 用 failed skill 的 error_signature 查询 (反查询值, 正常召回应中,
    # 但 filter 必须拦住 → 结果不含它)
    results = mgr.query_skill(
        error_signature="FAILED_sizing_plateau",
        context_pattern="blue_llm_eco_no_increment",
        top_k=5,
    )
    returned_names = [art.skill_name for _, art, _ in results]
    assert "failed_sizing_plateau" not in returned_names, (
        f"failed skill 被召回! 返回={returned_names}"
    )


# ─────────────────────────────────────────────────────────────────────────
#  4. distill 后 jsonl 行 schema 完整 (REQUIRED 5 字段齐)
# ─────────────────────────────────────────────────────────────────────────

def test_distilled_jsonl_row_has_required_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("ALLOW_REAL_MEMORY_WRITE", "0")

    import json
    from memory_manager import SkillMemoryManager
    from memory_integrity_guard import SkillVetting

    sublib = tmp_path / "backend_lib"
    sublib.mkdir()
    mgr = SkillMemoryManager(memory_root=sublib, _skip_integrity_check=True)
    guard = SkillVetting(domain="backend", audit_log=sublib / "audit.jsonl")

    payload = _make_b3_payload()
    happened, _, _ = guard.vet_distill(mgr, **payload)
    assert happened

    jsonl_path = sublib / "skill_index.jsonl"
    assert jsonl_path.exists(), "distill_skill 应自动 _append_to_jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    for field in ("skill_name", "precondition", "action_template",
                  "validation", "rollback_condition"):
        assert field in row, f"jsonl 行缺 REQUIRED 字段 {field}"
    # 失败签名嵌 precondition 内, 完整可读回
    assert row["precondition"]["is_failed_skill"] is True
    assert row["precondition"]["failure_signature"]["failure_class"] == "sizing_plateau"
    assert row["precondition"]["failure_signature"]["wns_trajectory"] == B3_WNS_TRAJECTORY
