module evaluate_sequence;
  reg clk = 0, rst_n = 0, data = 0;
  wire match, not_match;
  reg [31:0] rng = 32'h93a6c17d;
  reg [5:0] history = 0;
  integer phase = 0, index, checks = 0;
  TopModule dut(clk, rst_n, data, match, not_match);

  // An external stream monitor, independent of the DUT's state names/encoding.
  task observe(input bit_value);
    reg expected_match, expected_miss;
    begin
      clk = 0; data = bit_value; #3; clk = 1; #1;
      history = {history[4:0], bit_value};
      phase = phase + 1;
      expected_match = phase == 6 && history == 6'b011100;
      expected_miss = phase == 6 && history != 6'b011100;
      if (match !== expected_match || not_match !== expected_miss) begin
        $display("CHECK_FAIL evaluator sample=%0d phase=%0d history=%b expected=%b%b observed=%b%b",
                 checks, phase, history, expected_match, expected_miss, match, not_match);
        $finish;
      end
      checks = checks + 1;
      if (phase == 6) phase = 0;
      clk = 0; #3;
    end
  endtask

  initial begin
    #3; clk = 1; #3; clk = 0; #3; rst_n = 1;
    for (index = 0; index < 4096; index = index + 1) begin
      if (index % 257 == 256) begin
        rst_n = 0; #3; rst_n = 1; #3;
        history = 0; phase = 0;
        if ({match, not_match} !== 2'b00) begin
          $display("CHECK_FAIL evaluator asynchronous reset"); $finish;
        end
      end
      rng = rng ^ (rng << 13); rng = rng ^ (rng >> 17); rng = rng ^ (rng << 5);
      // Include matching groups as well as randomized mismatch paths.
      if (index % 96 < 6)
        observe((6'b011100 >> (5 - phase)) & 1);
      else observe(rng[9]);
    end
    $display("TESTBENCH PASS evaluator checks=%0d", checks);
    $finish;
  end
endmodule
