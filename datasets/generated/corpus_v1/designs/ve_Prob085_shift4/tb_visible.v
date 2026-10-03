`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg load;
  reg ena;
  reg [3:0] data;
  wire [3:0] q;
  TopModule dut(.clk(clk), .areset(areset), .load(load), .ena(ena), .data(data), .q(q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q[3],q[2],q[1],q[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset areset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,load,ena,data");
    load = 0;
    ena = 0;
    data = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      load = $random(s);
      ena = $random(s);
      data = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b", i, q[3], q[2], q[1], q[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", i, load, ena, data);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
