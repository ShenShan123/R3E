`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg [3:0] d;
  wire valid_out;
  wire dout;
  verified_parallel2serial dut(.clk(clk), .rst_n(rst_n), .d(d), .valid_out(valid_out), .dout(dout));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,valid_out,dout");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,d");
    d = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      d = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", $time, valid_out, dout);
      $fdisplay(r3e_stim, "%0d,%b", $time, d);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
