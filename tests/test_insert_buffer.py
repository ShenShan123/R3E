#!/usr/bin/env python3
"""
Test script for insert_buffer functionality
"""

import sys
sys.path.insert(0, '.')

from agentic_eco_engine import is_buffer_insertion_safe, commit_ok_insert_buffer

def test_buffer_insertion_safety():
    """Test buffer insertion safety checks"""
    print("Testing buffer insertion safety checks...")

    # Test 1: Valid buffer on data net
    is_safe, msg = is_buffer_insertion_safe("n123", "BUF_X2")
    assert is_safe, f"Data net with BUF_X2 should be safe: {msg}"
    print("✓ Test 1: Data net n123 with BUF_X2 is safe")

    # Test 2: Valid buffer with BUF_X4
    is_safe, msg = is_buffer_insertion_safe("data_path_net", "BUF_X4")
    assert is_safe, f"Data net with BUF_X4 should be safe: {msg}"
    print("✓ Test 2: Data net with BUF_X4 is safe")

    # Test 3: Reject clock net
    is_safe, msg = is_buffer_insertion_safe("clk", "BUF_X2")
    assert not is_safe, "Clock net should be rejected"
    assert "clk" in msg.lower()
    print("✓ Test 3: Clock net rejected")

    # Test 4: Reject reset net
    is_safe, msg = is_buffer_insertion_safe("rst_n", "BUF_X2")
    assert not is_safe, "Reset net should be rejected"
    assert "rst" in msg.lower()
    print("✓ Test 4: Reset net rejected")

    # Test 5: Reject scan net
    is_safe, msg = is_buffer_insertion_safe("scan_enable", "BUF_X2")
    assert not is_safe, "Scan net should be rejected"
    assert "scan" in msg.lower()
    print("✓ Test 5: Scan net rejected")

    # Test 6: Reject invalid buffer master
    is_safe, msg = is_buffer_insertion_safe("n123", "INV_X2")
    assert not is_safe, "Invalid buffer master should be rejected"
    assert "not in allowed list" in msg
    print("✓ Test 6: Invalid buffer master rejected")

    # Test 7: Reject buffer master with wrong name
    is_safe, msg = is_buffer_insertion_safe("n123", "BUF_X8")
    assert not is_safe, "BUF_X8 should be rejected (not in allowed list)"
    print("✓ Test 7: BUF_X8 rejected (not in allowed list)")

    # Test 8: Case-insensitive clock detection
    is_safe, msg = is_buffer_insertion_safe("CLK_main", "BUF_X2")
    assert not is_safe, "Clock net (uppercase) should be rejected"
    print("✓ Test 8: Clock net (uppercase) rejected")

    # Test 9: Test net detection
    is_safe, msg = is_buffer_insertion_safe("test_mode", "BUF_X2")
    assert not is_safe, "Test net should be rejected"
    print("✓ Test 9: Test net rejected")

    print("\n✅ All buffer insertion safety tests passed!")

def test_commit_policy():
    """Test insert_buffer commit policy"""
    print("\nTesting insert_buffer commit policy...")

    # Test 1: Good case - significant timing gain, moderate area
    # baseline=2000, 0.5% = 10, so area_limit=10
    assert commit_ok_insert_buffer(0.03, 0.10, 10.0, 2000.0)
    print("✓ Test 1: WNS=+0.03, TNS=+0.10, Area=+10, baseline=2000 -> commit")

    # Test 2: Good case - at threshold
    # baseline=1600, 0.5% = 8, so area_limit=8
    assert commit_ok_insert_buffer(0.02, 0.05, 8.0, 1600.0)
    print("✓ Test 2: WNS=+0.02, TNS=+0.05, Area=+8, baseline=1600 -> commit")

    # Test 3: Reject - insufficient WNS gain
    assert not commit_ok_insert_buffer(0.01, 0.10, 10.0, 2000.0)
    print("✓ Test 3: WNS=+0.01, TNS=+0.10, Area=+10 -> reject (WNS too small)")

    # Test 4: Reject - insufficient TNS gain
    assert not commit_ok_insert_buffer(0.03, 0.03, 10.0, 2000.0)
    print("✓ Test 4: WNS=+0.03, TNS=+0.03, Area=+10 -> reject (TNS too small)")

    # Test 5: Reject - area too large
    # baseline=2000, area_limit=10, so 15 is too large
    assert not commit_ok_insert_buffer(0.03, 0.10, 15.0, 2000.0)
    print("✓ Test 5: WNS=+0.03, TNS=+0.10, Area=+15 -> reject (area too large)")

    # Test 6: Area limit scales with baseline (small design)
    # baseline=100, 0.5% = 0.5, floor at 8
    assert commit_ok_insert_buffer(0.02, 0.05, 8.0, 100.0)
    assert not commit_ok_insert_buffer(0.02, 0.05, 9.0, 100.0)
    print("✓ Test 6: Area limit scales with baseline (small design, floor=8)")

    # Test 7: Area limit scales with baseline (large design)
    # baseline=10000, 0.5% = 50, ceiling at 30
    assert commit_ok_insert_buffer(0.02, 0.05, 30.0, 10000.0)
    assert not commit_ok_insert_buffer(0.02, 0.05, 31.0, 10000.0)
    print("✓ Test 7: Area limit scales with baseline (large design, ceiling=30)")

    # Test 8: Medium design - proportional limit
    # baseline=2000, 0.5% = 10
    assert commit_ok_insert_buffer(0.02, 0.05, 10.0, 2000.0)
    assert not commit_ok_insert_buffer(0.02, 0.05, 11.0, 2000.0)
    print("✓ Test 8: Area limit proportional for medium design")

    # Test 9: Reject WNS regression
    assert not commit_ok_insert_buffer(-0.01, 0.10, 10.0, 2000.0)
    print("✓ Test 9: WNS=-0.01, TNS=+0.10 -> reject (WNS regression)")

    print("\n✅ All commit policy tests passed!")

def test_area_limit_formula():
    """Test area limit formula: min(30, max(8, baseline * 0.005))"""
    print("\nTesting area limit formula...")

    # Test various baseline values
    test_cases = [
        (100, 8),      # 100 * 0.005 = 0.5 -> floor at 8
        (500, 8),      # 500 * 0.005 = 2.5 -> floor at 8
        (1600, 8),     # 1600 * 0.005 = 8.0 -> exactly 8
        (2000, 10),    # 2000 * 0.005 = 10.0
        (4000, 20),    # 4000 * 0.005 = 20.0
        (6000, 30),    # 6000 * 0.005 = 30.0 -> exactly 30
        (10000, 30),   # 10000 * 0.005 = 50.0 -> ceiling at 30
        (100000, 30),  # 100000 * 0.005 = 500.0 -> ceiling at 30
    ]

    for baseline, expected_limit in test_cases:
        actual_limit = min(30, max(8, baseline * 0.005))
        assert actual_limit == expected_limit, f"baseline={baseline}: expected {expected_limit}, got {actual_limit}"
        # Test at limit
        assert commit_ok_insert_buffer(0.02, 0.05, expected_limit, baseline)
        # Test above limit
        assert not commit_ok_insert_buffer(0.02, 0.05, expected_limit + 0.1, baseline)

    print(f"✓ All {len(test_cases)} area limit formula tests passed")

    print("\n✅ All area limit formula tests passed!")

if __name__ == "__main__":
    test_buffer_insertion_safety()
    test_commit_policy()
    test_area_limit_formula()
    print("\n" + "="*60)
    print("All insert_buffer tests passed successfully!")
    print("="*60)
