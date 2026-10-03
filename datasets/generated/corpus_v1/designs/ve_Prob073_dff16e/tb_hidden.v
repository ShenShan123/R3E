`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg resetn;
  reg [1:0] byteena;
  reg [15:0] d;
  wire [15:0] q;
  TopModule dut(.clk(clk), .resetn(resetn), .byteena(byteena), .d(d), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[15],q[14],q[13],q[12],q[11],q[10],q[9],q[8],q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    byteena = 0;
    d = 0;
    resetn = 0;
    repeat (2) @(negedge clk);
    resetn = 1;
    for (i = 0; i < 160; i = i + 1) begin
      byteena = $random(s);
      d = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, q[15], q[14], q[13], q[12], q[11], q[10], q[9], q[8], q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
