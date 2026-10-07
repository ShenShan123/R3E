`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg a;
  wire [2:0] q;
  TopModule dut(.clk(clk), .a(a), .q(q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q[2],q[1],q[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a");
    a = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", $time, q[2], q[1], q[0]);
      $fdisplay(r3e_stim, "%0d,%b", $time, a);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
