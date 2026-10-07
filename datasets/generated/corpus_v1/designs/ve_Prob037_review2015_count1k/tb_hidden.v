`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  wire [9:0] q;
  TopModule dut(.clk(clk), .reset(reset), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[9],q[8],q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, q[9], q[8], q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
