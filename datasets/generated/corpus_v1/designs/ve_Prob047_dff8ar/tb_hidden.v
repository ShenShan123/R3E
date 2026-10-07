`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg [7:0] d;
  wire [7:0] q;
  TopModule dut(.clk(clk), .d(d), .areset(areset), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    d = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      d = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", $time, q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
