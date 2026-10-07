`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg load;
  reg ena;
  reg [3:0] data;
  wire [3:0] q;
  TopModule dut(.clk(clk), .areset(areset), .load(load), .ena(ena), .data(data), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[3],q[2],q[1],q[0]");
    load = 0;
    ena = 0;
    data = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      load = $random(s);
      ena = $random(s);
      data = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, q[3], q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
