`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  wire clk_div;
  freq_divbyodd dut(.clk(clk), .rst_n(rst_n), .clk_div(clk_div));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,clk_div");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,");
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, clk_div);
      $fdisplay(r3e_stim, "%0d", $time);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
