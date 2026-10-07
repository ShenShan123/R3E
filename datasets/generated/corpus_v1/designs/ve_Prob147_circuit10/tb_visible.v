`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg a;
  reg b;
  wire q;
  wire state;
  TopModule dut(.clk(clk), .a(a), .b(b), .q(q), .state(state));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q,state");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b");
    a = 0;
    b = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", $time, q, state);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
