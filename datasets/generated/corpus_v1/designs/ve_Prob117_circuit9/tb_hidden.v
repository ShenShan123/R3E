`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg a;
  wire [2:0] q;
  TopModule dut(.clk(clk), .a(a), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[2],q[1],q[0]");
    a = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", i, q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
