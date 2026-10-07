`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg ar;
  reg d;
  wire q;
  TopModule dut(.clk(clk), .d(d), .ar(ar), .q(q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset ar held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,d");
    d = 0;
    ar = 1;
    repeat (2) @(negedge clk);
    ar = 0;
    for (i = 0; i < 64; i = i + 1) begin
      d = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, q);
      $fdisplay(r3e_stim, "%0d,%b", $time, d);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
