`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg data_in;
  wire sequence_detected;
  sequence_detector dut(.clk(clk), .rst_n(rst_n), .data_in(data_in), .sequence_detected(sequence_detected));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,sequence_detected");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,data_in");
    data_in = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      data_in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, sequence_detected);
      $fdisplay(r3e_stim, "%0d,%b", i, data_in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
