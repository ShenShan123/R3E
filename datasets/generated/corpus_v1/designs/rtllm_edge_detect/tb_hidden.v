`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg a;
  wire rise;
  wire down;
  verified_edge_detect dut(.clk(clk), .rst_n(rst_n), .a(a), .rise(rise), .down(down));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,rise,down");
    a = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", $time, rise, down);
    end
    $fclose(f);
    $finish;
  end
endmodule
