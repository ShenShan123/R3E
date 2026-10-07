`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg a;
  wire rise;
  wire down;
  verified_edge_detect dut(.clk(clk), .rst_n(rst_n), .a(a), .rise(rise), .down(down));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,rise,down");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,a");
    a = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", $time, rise, down);
      $fdisplay(r3e_stim, "%0d,%b", $time, a);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
