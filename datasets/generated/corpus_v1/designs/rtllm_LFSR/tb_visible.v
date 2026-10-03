`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst;
  wire [3:0] out;
  LFSR dut(.out(out), .clk(clk), .rst(rst));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,");
    rst = 1;
    repeat (2) @(negedge clk);
    rst = 0;
    for (i = 0; i < 64; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b", i, out[3], out[2], out[1], out[0]);
      $fdisplay(r3e_stim, "%0d", i);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
