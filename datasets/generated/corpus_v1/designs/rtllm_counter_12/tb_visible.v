`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg valid_count;
  wire [3:0] out;
  verified_counter_12 dut(.rst_n(rst_n), .clk(clk), .valid_count(valid_count), .out(out));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,valid_count");
    valid_count = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      valid_count = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, out[3], out[2], out[1], out[0]);
      $fdisplay(r3e_stim, "%0d,%b", $time, valid_count);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
