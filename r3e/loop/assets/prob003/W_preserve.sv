module tb;
  reg clk = 0, rst_n = 0, data = 0;
  wire match, not_match;
  integer i, j;
  reg [1:0] held;
  TopModule dut(clk, rst_n, data, match, not_match);

  task check_outputs;
    begin
      if ((match !== 0 && match !== 1) || (not_match !== 0 && not_match !== 1) ||
          (match && not_match)) begin
        $display("CHECK_FAIL W_preserve undefined or nonexclusive outputs");
        $finish;
      end
    end
  endtask

  task tick(input value);
    begin
      clk = 0; data = value; #2; clk = 1; #2;
      check_outputs;
      held = {match, not_match};
      data = ~value; #2;
      if ({match, not_match} !== held) begin
        $display("CHECK_FAIL W_preserve output changed without a clock edge");
        $finish;
      end
      clk = 0; #2;
    end
  endtask

  initial begin
    #2; clk = 1; #2; clk = 0; #2; rst_n = 1;
    for (i = 0; i < 64; i = i + 1)
      for (j = 5; j >= 0; j = j - 1) tick((i >> j) & 1);
    // Release reset with no clock edge after entering a terminal output state.
    rst_n = 0; #2; clk = 1; #2; clk = 0; #2; rst_n = 1;
    tick(0); tick(1); tick(1); tick(1); tick(0); tick(0);
    rst_n = 0; #2;
    if ({match, not_match} !== 2'b00) begin
      $display("CHECK_FAIL W_preserve reset outputs"); $finish;
    end
    rst_n = 1; #2;
    if ({match, not_match} !== 2'b00) begin
      $display("CHECK_FAIL W_preserve asynchronous reset did not clear state"); $finish;
    end
    $display("TESTBENCH PASS W_preserve reset exclusivity stability");
    $finish;
  end
endmodule
