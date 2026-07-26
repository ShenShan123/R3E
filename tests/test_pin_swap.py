#!/usr/bin/env python3
"""
Test script for pin_swap functionality
"""

import sys
sys.path.insert(0, '.')

from agentic_eco_engine import is_pin_swap_safe, commit_ok_pin_swap, COMMUTATIVE_PIN_GROUPS

def test_commutative_group_validation():
    """Test commutative pin group validation"""
    print("Testing commutative pin group validation...")

    # Test 1: Valid swap for NAND2_X2
    is_safe, msg = is_pin_swap_safe("NAND2_X2", "A1", "A2")
    assert is_safe, f"NAND2_X2 A1<->A2 should be safe: {msg}"
    print("✓ Test 1: NAND2_X2 A1<->A2 is safe")

    # Test 2: Valid swap for XOR2_X1 (uses A/B not A1/A2)
    is_safe, msg = is_pin_swap_safe("XOR2_X1", "A", "B")
    assert is_safe, f"XOR2_X1 A<->B should be safe: {msg}"
    print("✓ Test 2: XOR2_X1 A<->B is safe")

    # Test 3: Invalid swap for DFF (explicitly excluded)
    is_safe, msg = is_pin_swap_safe("DFF_X2", "D", "SI")
    assert not is_safe, "DFF_X2 D<->SI should be rejected"
    assert "explicitly excluded" in msg
    print("✓ Test 3: DFF_X2 D<->SI rejected (non-commutative)")

    # Test 4: Invalid swap for MUX (explicitly excluded)
    is_safe, msg = is_pin_swap_safe("MUX2_X1", "A", "S")
    assert not is_safe, "MUX2_X1 A<->S should be rejected"
    print("✓ Test 4: MUX2_X1 A<->S rejected (non-commutative)")

    # Test 5: Unknown master
    is_safe, msg = is_pin_swap_safe("UNKNOWN_CELL", "A", "B")
    assert not is_safe, "Unknown cell should be rejected"
    assert "not in COMMUTATIVE_PIN_GROUPS" in msg
    print("✓ Test 5: Unknown cell rejected")

    # Test 6: Wrong pin names for valid master
    is_safe, msg = is_pin_swap_safe("NAND2_X2", "A1", "B1")
    assert not is_safe, "NAND2_X2 A1<->B1 should be rejected (B1 doesn't exist)"
    print("✓ Test 6: Invalid pin names rejected")

    print("\n✅ All commutative group validation tests passed!")

def test_commit_policy():
    """Test pin_swap commit policy"""
    print("\nTesting pin_swap commit policy...")

    # Test 1: Good case - small WNS gain, zero area
    assert commit_ok_pin_swap(0.01, 0.02, 0.0)
    print("✓ Test 1: WNS=+0.01, TNS=+0.02, Area=0 -> commit")

    # Test 2: Good case - small TNS gain, zero area
    assert commit_ok_pin_swap(0.003, 0.05, 0.0)
    print("✓ Test 2: WNS=+0.003, TNS=+0.05, Area=0 -> commit")

    # Test 3: Reject - no timing gain
    assert not commit_ok_pin_swap(0.001, 0.01, 0.0)
    print("✓ Test 3: WNS=+0.001, TNS=+0.01, Area=0 -> reject (insufficient gain)")

    # Test 4: Reject - area increase too large
    assert not commit_ok_pin_swap(0.01, 0.05, 1.0)
    print("✓ Test 4: WNS=+0.01, TNS=+0.05, Area=+1.0 -> reject (area too large)")

    # Test 5: Edge case - exactly at threshold
    assert commit_ok_pin_swap(0.005, 0.02, 0.5)
    print("✓ Test 5: WNS=+0.005, TNS=+0.02, Area=+0.5 -> commit (at threshold)")

    # Test 6: Reject - WNS regression
    assert not commit_ok_pin_swap(-0.01, 0.05, 0.0)
    print("✓ Test 6: WNS=-0.01, TNS=+0.05, Area=0 -> reject (WNS regression)")

    print("\n✅ All commit policy tests passed!")

def test_commutative_pin_groups_coverage():
    """Test that COMMUTATIVE_PIN_GROUPS has expected coverage"""
    print("\nTesting COMMUTATIVE_PIN_GROUPS coverage...")

    # Test 1: Check basic 2-input gates are present
    expected_gates = [
        'NAND2_X1', 'NAND2_X2', 'NAND2_X4',
        'NOR2_X1', 'NOR2_X2', 'NOR2_X4',
        'AND2_X1', 'AND2_X2', 'AND2_X4',
        'OR2_X1', 'OR2_X2', 'OR2_X4',
        'XOR2_X1', 'XOR2_X2', 'XOR2_X4',
        'XNOR2_X1', 'XNOR2_X2', 'XNOR2_X4',
    ]

    for gate in expected_gates:
        assert gate in COMMUTATIVE_PIN_GROUPS, f"{gate} missing from COMMUTATIVE_PIN_GROUPS"
        assert len(COMMUTATIVE_PIN_GROUPS[gate]) > 0, f"{gate} should have pin groups"

    print(f"✓ Test 1: All {len(expected_gates)} expected commutative gates present")

    # Test 2: Check excluded gates are present with empty lists
    excluded_gates = [
        'MUX2_X1', 'MUX2_X2', 'MUX2_X4',
        'DFF_X1', 'DFF_X2', 'DFF_X4',
    ]

    for gate in excluded_gates:
        assert gate in COMMUTATIVE_PIN_GROUPS, f"{gate} should be in COMMUTATIVE_PIN_GROUPS (for explicit exclusion)"
        assert len(COMMUTATIVE_PIN_GROUPS[gate]) == 0, f"{gate} should have empty pin groups (excluded)"

    print(f"✓ Test 2: All {len(excluded_gates)} excluded gates present with empty lists")

    # Test 3: Check pin group structure
    assert COMMUTATIVE_PIN_GROUPS['NAND2_X2'] == [['A1', 'A2']]
    assert COMMUTATIVE_PIN_GROUPS['XOR2_X2'] == [['A', 'B']]
    print("✓ Test 3: Pin group structure is correct")

    print("\n✅ All coverage tests passed!")

if __name__ == "__main__":
    test_commutative_group_validation()
    test_commit_policy()
    test_commutative_pin_groups_coverage()
    print("\n" + "="*60)
    print("All pin_swap tests passed successfully!")
    print("="*60)
