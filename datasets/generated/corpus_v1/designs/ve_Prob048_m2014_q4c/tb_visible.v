`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg d;
  reg r;
  wire q;
  TopModule dut(.clk(clk), .d(d), .r(r), .q(q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,d,r");
    d = 0;
    r = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      d = $random(s);
      r = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, q);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, d, r);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
