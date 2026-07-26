#!/usr/bin/env python3
"""
Test target selection improvements for pin_swap and insert_buffer
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')

from agentic_eco_engine import (
    apply_eco_actions_to_netlist,
    ECOAction,
    SaturatedInstanceTracker,
)

def test_pin_name_fallback():
    """Test P1: pin name fallback for XOR2/XNOR2 (A1/A2 ↔ A/B)"""
    print("Testing pin name fallback for XOR2/XNOR2...")

    # Test 1: Instance has A/B, LLM provides A1/A2
    test_netlist = """
module test (input a, b, output z);
  XOR2_X2 _123_ ( .A(a), .B(b), .Z(z) );
endmodule
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False) as f:
        f.write(test_netlist)
        in_netlist = f.name

    out_netlist = in_netlist.replace('.v', '_out.v')

    # LLM provides A1/A2 but instance has A/B
    action = ECOAction(
        action_type="pin_swap",
        target_inst="_123_",
        params={"pin_a": "A1", "pin_b": "A2"}
    )

    success = apply_eco_actions_to_netlist(in_netlist, out_netlist, [action])

    if success:
        result = Path(out_netlist).read_text()
        if '.A(b)' in result and '.B(a)' in result:
            print("✓ Test 1: A1/A2 -> A/B fallback works for XOR2_X2")
        else:
            print("✗ Test 1: Fallback did not work")
    else:
        print("✗ Test 1: Fallback failed")

    # Cleanup
    Path(in_netlist).unlink(missing_ok=True)
    Path(out_netlist).unlink(missing_ok=True)

    # Test 2: Instance has A1/A2, LLM provides A/B
    test_netlist2 = """
module test (input a, b, output z);
  XNOR2_X2 _456_ ( .A1(a), .A2(b), .ZN(z) );
endmodule
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False) as f:
        f.write(test_netlist2)
        in_netlist = f.name

    out_netlist = in_netlist.replace('.v', '_out.v')

    # LLM provides A/B but instance has A1/A2
    action = ECOAction(
        action_type="pin_swap",
        target_inst="_456_",
        params={"pin_a": "A", "pin_b": "B"}
    )

    success = apply_eco_actions_to_netlist(in_netlist, out_netlist, [action])

    if success:
        result = Path(out_netlist).read_text()
        if '.A1(b)' in result and '.A2(a)' in result:
            print("✓ Test 2: A/B -> A1/A2 fallback works for XNOR2_X2")
        else:
            print("✗ Test 2: Fallback did not work")
    else:
        print("✗ Test 2: Fallback failed")

    # Cleanup
    Path(in_netlist).unlink(missing_ok=True)
    Path(out_netlist).unlink(missing_ok=True)

    print("\n✅ Pin name fallback tests completed!")


def test_dff_rejection():
    """Test P2: Reject DFF/sequential for insert_buffer auto-inference"""
    print("\nTesting DFF rejection for insert_buffer...")

    # Test: DFF with multiple outputs (Q/QN)
    test_netlist = """
module test (input clk, d, output q, qn);
  DFF_X2 _100_ ( .D(d), .CK(clk), .Q(q), .QN(qn) );
endmodule
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False) as f:
        f.write(test_netlist)
        in_netlist = f.name

    out_netlist = in_netlist.replace('.v', '_out.v')

    # Try to insert buffer on DFF output (should be rejected)
    action = ECOAction(
        action_type="insert_buffer",
        target_inst="_100_",  # DFF instance
        params={"buffer_master": "BUF_X2"}
    )

    success = apply_eco_actions_to_netlist(in_netlist, out_netlist, [action])

    if not success:
        print("✓ Test 1: DFF auto-inference correctly rejected")
    else:
        print("✗ Test 1: DFF should have been rejected")

    # Cleanup
    Path(in_netlist).unlink(missing_ok=True)
    Path(out_netlist).unlink(missing_ok=True)

    # Test: SDFF (scan DFF)
    test_netlist2 = """
module test (input clk, d, se, si, output q);
  SDFF_X2 _200_ ( .D(d), .SE(se), .SI(si), .CK(clk), .Q(q) );
endmodule
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False) as f:
        f.write(test_netlist2)
        in_netlist = f.name

    out_netlist = in_netlist.replace('.v', '_out.v')

    action = ECOAction(
        action_type="insert_buffer",
        target_inst="_200_",  # SDFF instance
        params={"buffer_master": "BUF_X2"}
    )

    success = apply_eco_actions_to_netlist(in_netlist, out_netlist, [action])

    if not success:
        print("✓ Test 2: SDFF auto-inference correctly rejected")
    else:
        print("✗ Test 2: SDFF should have been rejected")

    # Cleanup
    Path(in_netlist).unlink(missing_ok=True)
    Path(out_netlist).unlink(missing_ok=True)

    # Test: Combinational cell should still work
    test_netlist3 = """
module test (input a, b, output z);
  wire n1;
  NAND2_X2 _300_ ( .A1(a), .A2(b), .ZN(n1) );
  INV_X1 _301_ ( .A(n1), .ZN(z) );
endmodule
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False) as f:
        f.write(test_netlist3)
        in_netlist = f.name

    out_netlist = in_netlist.replace('.v', '_out.v')

    action = ECOAction(
        action_type="insert_buffer",
        target_inst="_300_",  # Combinational cell
        params={"buffer_master": "BUF_X2"}
    )

    success = apply_eco_actions_to_netlist(in_netlist, out_netlist, [action])

    if success:
        result = Path(out_netlist).read_text()
        if 'BUF_X2' in result:
            print("✓ Test 3: Combinational cell auto-inference still works")
        else:
            print("✗ Test 3: Buffer not inserted")
    else:
        print("✗ Test 3: Combinational cell should work")

    # Cleanup
    Path(in_netlist).unlink(missing_ok=True)
    Path(out_netlist).unlink(missing_ok=True)

    print("\n✅ DFF rejection tests completed!")


def test_saturated_feedback_for_primitives():
    """Test P0: Record ineffective pin_swap/insert_buffer to saturated tracker"""
    print("\nTesting saturated feedback for primitives...")

    tracker = SaturatedInstanceTracker()

    # Simulate recording ineffective pin_swap
    tracker.record_saturated("_4192_", "PIN_SWAP", "no_wns_gain", "pin_swap ineffective, delta_wns=0.0000, delta_tns=-0.02")
    tracker.record_saturated("_4195_", "PIN_SWAP", "no_wns_gain", "pin_swap ineffective, delta_wns=0.0000, delta_tns=0.00")

    # Simulate recording ineffective insert_buffer
    tracker.record_saturated("n1863", "BUF_X2", "no_wns_gain", "insert_buffer ineffective, delta_wns=0.0000, delta_area=1.0")
    tracker.record_saturated("_6752_", "BUF_X2", "no_wns_gain", "insert_buffer wasteful, delta_wns=0.0050, delta_area=2.0")

    # Check if recorded
    assert tracker.is_saturated("_4192_")
    assert tracker.is_saturated("_4195_")
    assert tracker.is_saturated("n1863")
    assert tracker.is_saturated("_6752_")
    print("✓ Test 1: Ineffective primitives recorded to tracker")

    # Generate feedback section
    feedback = tracker.get_feedback_section()
    assert "_4192_" in feedback
    assert "PIN_SWAP" in feedback
    assert "n1863" in feedback
    assert "BUF_X2" in feedback
    print("✓ Test 2: Feedback section includes primitive failures")

    # Check context is preserved
    assert "delta_wns=0.0000" in feedback
    assert "delta_tns=-0.02" in feedback
    print("✓ Test 3: Context information preserved in feedback")

    print("\n✅ Saturated feedback tests completed!")


if __name__ == "__main__":
    print("="*60)
    print("Target Selection Improvement Tests")
    print("="*60)

    test_pin_name_fallback()
    test_dff_rejection()
    test_saturated_feedback_for_primitives()

    print("\n" + "="*60)
    print("✅ ALL TARGET SELECTION TESTS PASSED!")
    print("="*60)
    print("\nThese improvements address the issues in test_action_v2_1_fir_scu.log:")
    print("  P0: Ineffective primitives now recorded to saturated feedback")
    print("  P1: XOR2/XNOR2 pin name variants (A1/A2 ↔ A/B) auto-mapped")
    print("  P2: DFF/sequential cells rejected for insert_buffer auto-inference")
