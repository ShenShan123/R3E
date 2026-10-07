`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg x;
  wire z;
  TopModule dut(.clk(clk), .areset(areset), .x(x), .z(z));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,z");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset areset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,x");
    x = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      x = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, z);
      $fdisplay(r3e_stim, "%0d,%b", $time, x);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
