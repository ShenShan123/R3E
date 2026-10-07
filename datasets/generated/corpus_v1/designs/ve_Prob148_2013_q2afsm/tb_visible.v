`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg resetn;
  reg [2:0] r;
  wire [2:0] g;
  TopModule dut(.clk(clk), .resetn(resetn), .r(r), .g(g));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,g[2],g[1],g[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset resetn held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,r");
    r = 0;
    resetn = 0;
    repeat (2) @(negedge clk);
    resetn = 1;
    for (i = 0; i < 64; i = i + 1) begin
      r = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", $time, g[2], g[1], g[0]);
      $fdisplay(r3e_stim, "%0d,%b", $time, r);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
