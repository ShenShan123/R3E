`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg resetn;
  reg [2:0] r;
  wire [2:0] g;
  TopModule dut(.clk(clk), .resetn(resetn), .r(r), .g(g));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,g[2],g[1],g[0]");
    r = 0;
    resetn = 0;
    repeat (2) @(negedge clk);
    resetn = 1;
    for (i = 0; i < 160; i = i + 1) begin
      r = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", i, g[2], g[1], g[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
