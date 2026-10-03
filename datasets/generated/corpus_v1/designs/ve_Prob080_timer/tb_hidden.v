`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg load;
  reg [9:0] data;
  wire tc;
  TopModule dut(.clk(clk), .load(load), .data(data), .tc(tc));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,tc");
    load = 0;
    data = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      load = $random(s);
      data = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, tc);
    end
    $fclose(f);
    $finish;
  end
endmodule
