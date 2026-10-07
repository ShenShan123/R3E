`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg [7:0] data_in;
  reg valid_in;
  wire valid_out;
  wire [9:0] data_out;
  verified_accu dut(.clk(clk), .rst_n(rst_n), .data_in(data_in), .valid_in(valid_in), .valid_out(valid_out), .data_out(data_out));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,valid_out,data_out[9],data_out[8],data_out[7],data_out[6],data_out[5],data_out[4],data_out[3],data_out[2],data_out[1],data_out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,data_in,valid_in");
    data_in = 0;
    valid_in = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      data_in = $random(s);
      valid_in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, valid_out, data_out[9], data_out[8], data_out[7], data_out[6], data_out[5], data_out[4], data_out[3], data_out[2], data_out[1], data_out[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, data_in, valid_in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
