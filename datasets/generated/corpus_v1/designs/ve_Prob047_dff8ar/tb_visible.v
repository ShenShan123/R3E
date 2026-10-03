`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg [7:0] d;
  wire [7:0] q;
  TopModule dut(.clk(clk), .d(d), .areset(areset), .q(q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset areset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,d");
    d = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      d = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", i, q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
      $fdisplay(r3e_stim, "%0d,%b", i, d);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
