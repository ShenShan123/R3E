#!/usr/bin/env python3
"""
Test script for ToolModeBlacklist functionality
"""

import sys
sys.path.insert(0, '.')

from agentic_eco_engine import ToolModeBlacklist

def test_basic_functionality():
    """Test basic blacklist functionality"""
    print("Testing ToolModeBlacklist basic functionality...")

    blacklist = ToolModeBlacklist()
    design = "fir_scu"
    wns = -0.1100
    tns = -5.5

    # Test 1: Initially no modes should be blocked
    assert not blacklist.should_skip(design, wns, tns, "tns_focused")
    print("✓ Test 1: Initially no modes blocked")

    # Test 2: Record first result with ΔWNS=0
    blacklist.record_result(design, wns, tns, "tns_focused", 0.0001)
    assert not blacklist.should_skip(design, wns, tns, "tns_focused")
    print("✓ Test 2: Single no-op doesn't trigger blacklist")

    # Test 3: Record second consecutive result with ΔWNS=0
    blacklist.record_result(design, wns, tns, "tns_focused", 0.0001)
    assert blacklist.should_skip(design, wns, tns, "tns_focused")
    print("✓ Test 3: Two consecutive no-ops trigger blacklist")

    # Test 4: Other modes should not be blocked
    assert not blacklist.should_skip(design, wns, tns, "last_gasp")
    print("✓ Test 4: Other modes not affected")

    # Test 5: Different state should not be blocked
    assert not blacklist.should_skip(design, -0.2000, tns, "tns_focused")
    print("✓ Test 5: Different WNS state not blocked")

    # Test 6: Get blocked modes
    blocked = blacklist.get_blocked_modes(design, wns, tns)
    assert "tns_focused" in blocked
    assert len(blocked) == 1
    print(f"✓ Test 6: get_blocked_modes returns {blocked}")

    print("\n✅ All tests passed!")

def test_state_signature():
    """Test state signature generation"""
    print("\nTesting state signature generation...")

    blacklist = ToolModeBlacklist()

    # Test rounding - values that round to same signature
    sig1 = blacklist._make_state_sig("test", -0.1140, -5.54)
    sig2 = blacklist._make_state_sig("test", -0.1149, -5.54)

    # Both should round to same signature
    expected = "test|wns=-0.11|tns=-5.5"
    assert sig1 == expected, f"Expected {expected}, got {sig1}"
    assert sig2 == expected, f"Expected {expected}, got {sig2}"
    print(f"✓ State signature rounding works: {sig1}")

    # Different states should have different signatures
    sig3 = blacklist._make_state_sig("test", -0.1249, -5.54)
    expected3 = "test|wns=-0.12|tns=-5.5"
    assert sig3 == expected3, f"Expected {expected3}, got {sig3}"
    assert sig3 != sig1, f"sig3={sig3} should differ from sig1={sig1}"
    print(f"✓ Different states have different signatures: {sig3}")

    print("\n✅ State signature tests passed!")

def test_multiple_modes():
    """Test blocking multiple modes"""
    print("\nTesting multiple mode blocking...")

    blacklist = ToolModeBlacklist()
    design = "test_design"
    wns = -0.15
    tns = -10.0

    # Block tns_focused
    blacklist.record_result(design, wns, tns, "tns_focused", 0.0)
    blacklist.record_result(design, wns, tns, "tns_focused", 0.0)

    # Block last_gasp
    blacklist.record_result(design, wns, tns, "last_gasp", 0.0005)
    blacklist.record_result(design, wns, tns, "last_gasp", 0.0003)

    # Block overconstrained_20ps
    blacklist.record_result(design, wns, tns, "overconstrained_20ps", 0.0)
    blacklist.record_result(design, wns, tns, "overconstrained_20ps", 0.0001)

    blocked = blacklist.get_blocked_modes(design, wns, tns)
    assert len(blocked) == 3
    assert "tns_focused" in blocked
    assert "last_gasp" in blocked
    assert "overconstrained_20ps" in blocked

    print(f"✓ Multiple modes blocked: {blocked}")
    print("\n✅ Multiple mode blocking tests passed!")

if __name__ == "__main__":
    test_basic_functionality()
    test_state_signature()
    test_multiple_modes()
    print("\n" + "="*60)
    print("All ToolModeBlacklist tests passed successfully!")
    print("="*60)
