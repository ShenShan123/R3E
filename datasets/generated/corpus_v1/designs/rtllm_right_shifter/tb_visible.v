`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg d;
  wire [7:0] q;
  verified_right_shifter dut(.clk(clk), .q(q), .d(d));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,d");
    d = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      d = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", $time, q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
      $fdisplay(r3e_stim, "%0d,%b", $time, d);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
