#!/usr/bin/env python3
"""
Integration test for Action Space v2.1
Tests the complete flow: SaturatedInstanceTracker + build_eco_prompt + action execution
"""

import sys
sys.path.insert(0, '.')

from agentic_eco_engine import (
    SaturatedInstanceTracker,
    build_eco_prompt,
    is_pin_swap_safe,
    is_buffer_insertion_safe,
    commit_ok_pin_swap,
    commit_ok_insert_buffer,
    COMMUTATIVE_PIN_GROUPS
)

def test_saturated_tracker_in_prompt():
    """Test that SaturatedInstanceTracker integrates with build_eco_prompt"""
    print("Testing SaturatedInstanceTracker integration with build_eco_prompt...")

    # Create tracker and record some saturated instances
    tracker = SaturatedInstanceTracker()
    tracker.record_saturated("_4192_", "XOR2_X2", "no_legal_upsize", "")
    tracker.record_saturated("_6752_", "DFF_X2", "no_wns_gain", "delta_wns=0.0001")
    tracker.record_saturated("_8901_", "NAND2_X4", "tns_regressed", "delta_tns=-0.05")

    # Build prompt with tracker
    prompt = build_eco_prompt(
        design_name="test_design",
        wns=-0.1,
        tns=-5.0,
        area=1000.0,
        timing_report="Test timing report",
        saturated_tracker=tracker
    )

    # Verify saturated section is in prompt
    assert "No-op / Saturated Instances" in prompt
    assert "_4192_" in prompt
    assert "XOR2_X2" in prompt
    assert "_6752_" in prompt
    assert "_8901_" in prompt
    assert "no larger cell available" in prompt
    assert "no WNS gain" in prompt
    assert "TNS regression" in prompt

    print("✓ SaturatedInstanceTracker feedback appears in prompt")
    print("✓ All three categories (no_legal_upsize, no_wns_gain, tns_regressed) present")

    # Verify Action Selection Guide is in prompt
    assert "Action Selection Guide" in prompt
    assert "pin_swap" in prompt
    assert "insert_buffer" in prompt
    print("✓ Action Selection Guide present in prompt")

    # Verify new actions are enabled
    assert "pin_swap: Swap commutative input pins" in prompt or "pin_swap" in prompt
    assert "insert_buffer: Insert buffer on high-fanout net" in prompt or "insert_buffer" in prompt
    assert "✅ ENABLED - P1" in prompt or "✅ ENABLED - P2" in prompt
    print("✓ New actions (pin_swap, insert_buffer) are enabled in prompt")

    print("\n✅ Integration test passed!")

def test_action_validation():
    """Test action validation functions"""
    print("\nTesting action validation functions...")

    # Test pin_swap validation
    is_safe, msg = is_pin_swap_safe("NAND2_X2", "A1", "A2")
    assert is_safe
    print("✓ pin_swap validation: NAND2_X2 A1<->A2 is safe")

    is_safe, msg = is_pin_swap_safe("DFF_X2", "D", "SI")
    assert not is_safe
    print("✓ pin_swap validation: DFF_X2 D<->SI is rejected")

    # Test insert_buffer validation
    is_safe, msg = is_buffer_insertion_safe("n123", "BUF_X2")
    assert is_safe
    print("✓ insert_buffer validation: data net with BUF_X2 is safe")

    is_safe, msg = is_buffer_insertion_safe("clk", "BUF_X2")
    assert not is_safe
    print("✓ insert_buffer validation: clock net is rejected")

    print("\n✅ Action validation tests passed!")

def test_commit_policies():
    """Test commit policies for new actions"""
    print("\nTesting commit policies...")

    # Test pin_swap policy
    assert commit_ok_pin_swap(0.01, 0.05, 0.0)
    print("✓ pin_swap policy: WNS=+0.01, TNS=+0.05, Area=0 -> commit")

    assert not commit_ok_pin_swap(0.001, 0.01, 0.0)
    print("✓ pin_swap policy: insufficient gain -> reject")

    # Test insert_buffer policy
    assert commit_ok_insert_buffer(0.03, 0.10, 10.0, 2000.0)
    print("✓ insert_buffer policy: good timing gain -> commit")

    assert not commit_ok_insert_buffer(0.01, 0.10, 10.0, 2000.0)
    print("✓ insert_buffer policy: insufficient WNS gain -> reject")

    print("\n✅ Commit policy tests passed!")

def test_prompt_structure():
    """Test that prompt has correct structure with all sections"""
    print("\nTesting prompt structure...")

    tracker = SaturatedInstanceTracker()
    tracker.record_saturated("_100_", "BUF_X4", "no_legal_upsize", "")

    prompt = build_eco_prompt(
        design_name="test",
        wns=-0.05,
        tns=-2.0,
        area=500.0,
        timing_report="Test report",
        saturated_tracker=tracker,
        blocked_tool_modes={"tns_focused"},
        post_tool_plateau=True
    )

    # Check all major sections
    sections = [
        "Current Design Status",
        "No-op / Saturated Instances",
        "POST-TOOL PLATEAU NOTICE",
        "Action Selection Guide",
        "Available Repair Primitives",
        "CRITICAL CONSTRAINTS",
    ]

    for section in sections:
        assert section in prompt, f"Missing section: {section}"
        print(f"✓ Section present: {section}")

    # Check action count
    assert "size_cell" in prompt
    assert "resize_chain" in prompt
    assert "tool_repair" in prompt
    assert "pin_swap" in prompt
    assert "insert_buffer" in prompt
    print("✓ All 5 action types present in prompt")

    print("\n✅ Prompt structure tests passed!")

def test_commutative_groups_coverage():
    """Test COMMUTATIVE_PIN_GROUPS has good coverage"""
    print("\nTesting COMMUTATIVE_PIN_GROUPS coverage...")

    # Count enabled vs excluded
    enabled = sum(1 for groups in COMMUTATIVE_PIN_GROUPS.values() if groups)
    excluded = sum(1 for groups in COMMUTATIVE_PIN_GROUPS.values() if not groups)

    print(f"✓ Enabled gates: {enabled}")
    print(f"✓ Explicitly excluded gates: {excluded}")

    assert enabled >= 18, "Should have at least 18 commutative gates"
    assert excluded >= 6, "Should have at least 6 explicitly excluded gates"

    print("\n✅ COMMUTATIVE_PIN_GROUPS coverage test passed!")

if __name__ == "__main__":
    print("="*60)
    print("Action Space v2.1 Integration Tests")
    print("="*60)

    test_saturated_tracker_in_prompt()
    test_action_validation()
    test_commit_policies()
    test_prompt_structure()
    test_commutative_groups_coverage()

    print("\n" + "="*60)
    print("✅ ALL INTEGRATION TESTS PASSED!")
    print("="*60)
    print("\nAction Space v2.1 is ready for deployment.")
    print("Next steps:")
    print("  1. Complete Task #2: Integrate feedback collection in candidate evaluation")
    print("  2. Complete Task #16: Create v2.1 experiment script")
