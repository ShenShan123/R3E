from microsurgeon_flow.finish_rpt_parser import parse_reg2reg_cell_chain


_SAMPLE = """
Startpoint: dpath.b_reg.out[0]$_DFFE_PP_ (rising edge-triggered ...)
Endpoint: dpath.b_reg.out[5]$_DFFE_PP_ (rising edge-triggered ...)
Path Type: max
finish report_checks -path_delay max reg to reg

   Delay    Time   Description
   0.00    0.00 ^ dpath.b_reg.out[0]$_DFFE_PP_/CK (DFF_X1)
   0.05    0.16 ^ dpath.b_reg.out[0]$_DFFE_PP_/Q (DFF_X1)
   0.03    0.19 ^ _568_/ZN (NAND2_X2)
   0.01    0.20 v _569_/ZN (INV_X4)
   0.00    0.52 ^ dpath.b_reg.out[5]$_DFFE_PP_/D (DFF_X1)
           0.52   data arrival time

   0.46    0.46   clock ... (capture)
   0.01    0.47 ^ clkbuf_0_clk/Z (CLKBUF_X1)
           0.46   data required time
"""


def test_chain_is_ordered_and_complete():
    chain = parse_reg2reg_cell_chain(_SAMPLE)
    insts = [cell["inst"] for cell in chain]
    assert insts == [
        "dpath.b_reg.out[0]$_DFFE_PP_",
        "dpath.b_reg.out[0]$_DFFE_PP_",
        "_568_",
        "_569_",
        "dpath.b_reg.out[5]$_DFFE_PP_",
    ]


def test_stops_at_data_arrival():
    chain = parse_reg2reg_cell_chain(_SAMPLE)
    masters = [cell["master"] for cell in chain]
    assert "CLKBUF_X1" not in masters


def test_fields_parsed():
    chain = parse_reg2reg_cell_chain(_SAMPLE)
    nand = next(cell for cell in chain if cell["inst"] == "_568_")
    assert nand == {
        "inst": "_568_",
        "pin": "ZN",
        "master": "NAND2_X2",
        "edge": "^",
        "delay": 0.03,
        "time": 0.19,
    }


def test_no_section_returns_empty():
    assert parse_reg2reg_cell_chain("garbage no section here") == []


def test_missing_data_arrival_returns_empty():
    assert parse_reg2reg_cell_chain(
        """finish report_checks -path_delay max reg to reg
   0.03    0.19 ^ _568_/ZN (NAND2_X2)
"""
    ) == []
