create_clock -name clk -period 1.6 [get_ports N1]
set_input_delay 0.0 -clock clk [all_inputs]
set_output_delay 0.0 -clock clk [all_outputs]
