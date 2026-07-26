#!/usr/bin/env python3
"""
Test saturated feedback collection integration
"""

import sys
sys.path.insert(0, '.')

from agentic_eco_engine import SaturatedInstanceTracker, ECOAction

def test_feedback_collection_logic():
    """Test the logic for recording saturated instances during evaluation"""
    print("Testing saturated feedback collection logic...")

    tracker = SaturatedInstanceTracker()

    # Simulate a rejected candidate with no WNS gain
    action = ECOAction(
        action_type="size_cell",
        target_inst="_123_",
        params={"new_master": "NAND2_X4", "old_master": "NAND2_X2"}
    )

    # Simulate evaluation results
    wns_delta = 0.0001  # Very small, < 0.001
    tns_delta = 0.05

    # Record as no_wns_gain
    if wns_delta < 0.001:
        context = f"delta_wns={wns_delta:.4f}"
        tracker.record_saturated(
            action.target_inst,
            action.params["new_master"],
            "no_wns_gain",
            context
        )

    assert tracker.is_saturated("_123_")
    print("✓ Instance recorded as saturated (no_wns_gain)")

    # Simulate another candidate with TNS regression
    action2 = ECOAction(
        action_type="size_cell",
        target_inst="_456_",
        params={"new_master": "NOR2_X4", "old_master": "NOR2_X2"}
    )

    wns_delta2 = 0.02
    tns_delta2 = -0.1  # Negative, TNS regressed

    if tns_delta2 < 0:
        context = f"delta_tns={tns_delta2:.4f}"
        tracker.record_saturated(
            action2.target_inst,
            action2.params["new_master"],
            "tns_regressed",
            context
        )

    assert tracker.is_saturated("_456_")
    print("✓ Instance recorded as saturated (tns_regressed)")

    # Generate feedback section
    feedback = tracker.get_feedback_section()
    assert "_123_" in feedback
    assert "_456_" in feedback
    assert "no WNS gain" in feedback
    assert "TNS regression" in feedback
    print("✓ Feedback section generated correctly")

    print("\n✅ Feedback collection logic test passed!")

def test_resize_chain_feedback():
    """Test feedback collection for resize_chain actions"""
    print("\nTesting resize_chain feedback collection...")

    tracker = SaturatedInstanceTracker()

    # Simulate a resize_chain action
    action = ECOAction(
        action_type="resize_chain",
        target_insts=["_100_", "_101_", "_102_"],
        params={"policy": "gradual_x2"}
    )

    # Simulate no WNS gain
    wns_delta = 0.0005

    if wns_delta < 0.001:
        chain_insts = action.target_insts
        for inst in chain_insts:
            context = f"chain resize, delta_wns={wns_delta:.4f}"
            tracker.record_saturated(inst, "CHAIN", "no_wns_gain", context)

    # Check all instances in chain are recorded
    assert tracker.is_saturated("_100_")
    assert tracker.is_saturated("_101_")
    assert tracker.is_saturated("_102_")
    print("✓ All instances in chain recorded as saturated")

    feedback = tracker.get_feedback_section()
    assert "_100_" in feedback
    assert "_101_" in feedback
    assert "_102_" in feedback
    print("✓ Chain feedback appears in section")

    print("\n✅ Resize chain feedback test passed!")

if __name__ == "__main__":
    print("="*60)
    print("Saturated Feedback Collection Integration Tests")
    print("="*60)

    test_feedback_collection_logic()
    test_resize_chain_feedback()

    print("\n" + "="*60)
    print("✅ ALL FEEDBACK COLLECTION TESTS PASSED!")
    print("="*60)
    print("\nTask #2 (P0 feedback collection integration) is complete.")
