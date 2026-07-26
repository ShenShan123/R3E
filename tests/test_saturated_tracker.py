#!/usr/bin/env python3
"""
Test script for SaturatedInstanceTracker functionality
"""

import sys
sys.path.insert(0, '.')

from agentic_eco_engine import SaturatedInstanceTracker

def test_basic_functionality():
    """Test basic saturated instance tracking"""
    print("Testing SaturatedInstanceTracker basic functionality...")

    tracker = SaturatedInstanceTracker()

    # Test 1: Initially no instances should be saturated
    assert not tracker.is_saturated("_4192_")
    print("✓ Test 1: Initially no instances saturated")

    # Test 2: Record a no_legal_upsize instance
    tracker.record_saturated("_4192_", "XOR2_X2", "no_legal_upsize", "")
    assert tracker.is_saturated("_4192_")
    print("✓ Test 2: Instance recorded as saturated")

    # Test 3: Other instances should not be saturated
    assert not tracker.is_saturated("_6752_")
    print("✓ Test 3: Other instances not affected")

    # Test 4: Record a no_wns_gain instance
    tracker.record_saturated("_6752_", "DFF_X2", "no_wns_gain", "delta_wns=0.0001")
    assert tracker.is_saturated("_6752_")
    print("✓ Test 4: no_wns_gain instance recorded")

    # Test 5: Record a tns_regressed instance
    tracker.record_saturated("_8901_", "NAND2_X4", "tns_regressed", "delta_tns=-0.05")
    assert tracker.is_saturated("_8901_")
    print("✓ Test 5: tns_regressed instance recorded")

    print("\n✅ All basic tests passed!")

def test_feedback_section_generation():
    """Test feedback section generation for prompt"""
    print("\nTesting feedback section generation...")

    tracker = SaturatedInstanceTracker()

    # Test 1: Empty tracker should return empty string
    feedback = tracker.get_feedback_section()
    assert feedback == ""
    print("✓ Test 1: Empty tracker returns empty string")

    # Test 2: Add instances and generate feedback
    tracker.record_saturated("_4192_", "XOR2_X2", "no_legal_upsize", "")
    tracker.record_saturated("_6752_", "DFF_X2", "no_wns_gain", "delta_wns=0.0001")
    tracker.record_saturated("_8901_", "NAND2_X4", "tns_regressed", "delta_tns=-0.05")

    feedback = tracker.get_feedback_section()
    assert "No-op / Saturated Instances" in feedback
    assert "_4192_" in feedback
    assert "XOR2_X2" in feedback
    assert "no larger cell available" in feedback
    assert "_6752_" in feedback
    assert "no WNS gain" in feedback
    assert "_8901_" in feedback
    assert "TNS regression" in feedback
    print("✓ Test 2: Feedback section contains all instances")

    # Test 3: Check structure
    assert "**No legal upsize available:**" in feedback
    assert "**Previous resize produced no WNS gain:**" in feedback
    assert "**Previous resize caused TNS regression:**" in feedback
    assert "CRITICAL" in feedback
    print("✓ Test 3: Feedback section has correct structure")

    print("\n✅ Feedback section tests passed!")

def test_multiple_categories():
    """Test tracking multiple instances in different categories"""
    print("\nTesting multiple categories...")

    tracker = SaturatedInstanceTracker()

    # Add multiple instances in each category
    tracker.record_saturated("_100_", "BUF_X4", "no_legal_upsize", "")
    tracker.record_saturated("_101_", "INV_X8", "no_legal_upsize", "")

    tracker.record_saturated("_200_", "NAND2_X1", "no_wns_gain", "delta_wns=0.0")
    tracker.record_saturated("_201_", "NOR2_X2", "no_wns_gain", "delta_wns=0.0005")

    tracker.record_saturated("_300_", "AND2_X4", "tns_regressed", "delta_tns=-0.1")

    # Check all are tracked
    assert tracker.is_saturated("_100_")
    assert tracker.is_saturated("_101_")
    assert tracker.is_saturated("_200_")
    assert tracker.is_saturated("_201_")
    assert tracker.is_saturated("_300_")
    print("✓ All instances tracked correctly")

    # Check feedback includes all
    feedback = tracker.get_feedback_section()
    assert "_100_" in feedback and "_101_" in feedback
    assert "_200_" in feedback and "_201_" in feedback
    assert "_300_" in feedback
    print("✓ Feedback includes all categories")

    print("\n✅ Multiple category tests passed!")

def test_overwrite_behavior():
    """Test that recording same instance twice overwrites"""
    print("\nTesting overwrite behavior...")

    tracker = SaturatedInstanceTracker()

    # Record instance with one reason
    tracker.record_saturated("_123_", "BUF_X2", "no_legal_upsize", "")
    feedback1 = tracker.get_feedback_section()
    assert "no larger cell available" in feedback1

    # Record same instance with different reason
    tracker.record_saturated("_123_", "BUF_X4", "no_wns_gain", "delta_wns=0.0")
    feedback2 = tracker.get_feedback_section()
    assert "no WNS gain" in feedback2
    assert "BUF_X4" in feedback2  # Should have new master
    print("✓ Instance overwrite works correctly")

    print("\n✅ Overwrite tests passed!")

if __name__ == "__main__":
    test_basic_functionality()
    test_feedback_section_generation()
    test_multiple_categories()
    test_overwrite_behavior()
    print("\n" + "="*60)
    print("All SaturatedInstanceTracker tests passed successfully!")
    print("="*60)
