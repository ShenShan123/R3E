module tb;
  reg clk = 0, rst_n = 0, data = 0;
  wire match, not_match;
  integer group_index, bit_index, cycle = 0;
  reg [5:0] word;
  TopModule dut(clk, rst_n, data, match, not_match);

  task reset;
    begin
      clk = 0; rst_n = 0; #2;
      clk = 1; #2; clk = 0; #2; rst_n = 1;
      cycle = 0;
    end
  endtask

  task group(input [5:0] bits);
    begin
      for (bit_index = 5; bit_index >= 0; bit_index = bit_index - 1) begin
        clk = 0; data = bits[bit_index]; #2; clk = 1; #2;
        cycle = cycle + 1;
        if ((bit_index != 0 && {match, not_match} !== 2'b00) ||
            (bit_index == 0 && {match, not_match} !== (bits == 6'b011100 ? 2'b10 : 2'b01))) begin
          $display("CHECK_FAIL W_bug cycle=%0d group=%b bit=%0d match=%b not_match=%b",
                   cycle, bits, bit_index, match, not_match);
          $finish;
        end
        clk = 0; #2;
      end
    end
  endtask

  initial begin
    reset;
    for (group_index = 0; group_index < 64; group_index = group_index + 1) begin
      word = group_index;
      group(6'b011100);
      group(word);
      group(6'b100011);
      group(6'b011100);
    end
    $display("TESTBENCH PASS W_bug groups=256 cycles=%0d", cycle);
    $finish;
  end
endmodule
