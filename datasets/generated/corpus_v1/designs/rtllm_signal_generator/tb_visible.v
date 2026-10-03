`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  wire [4:0] wave;
  verified_signal_generator dut(.clk(clk), .rst_n(rst_n), .wave(wave));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,wave[4],wave[3],wave[2],wave[1],wave[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,");
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", i, wave[4], wave[3], wave[2], wave[1], wave[0]);
      $fdisplay(r3e_stim, "%0d", i);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
