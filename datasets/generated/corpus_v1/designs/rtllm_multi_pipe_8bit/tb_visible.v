`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg [7:0] mul_a;
  reg [7:0] mul_b;
  reg mul_en_in;
  wire mul_en_out;
  wire [15:0] mul_out;
  verified_multi_pipe_8bit dut(.clk(clk), .rst_n(rst_n), .mul_a(mul_a), .mul_b(mul_b), .mul_en_in(mul_en_in), .mul_en_out(mul_en_out), .mul_out(mul_out));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,mul_en_out,mul_out[15],mul_out[14],mul_out[13],mul_out[12],mul_out[11],mul_out[10],mul_out[9],mul_out[8],mul_out[7],mul_out[6],mul_out[5],mul_out[4],mul_out[3],mul_out[2],mul_out[1],mul_out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,mul_a,mul_b,mul_en_in");
    mul_a = 0;
    mul_b = 0;
    mul_en_in = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      mul_a = $random(s);
      mul_b = $random(s);
      mul_en_in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, mul_en_out, mul_out[15], mul_out[14], mul_out[13], mul_out[12], mul_out[11], mul_out[10], mul_out[9], mul_out[8], mul_out[7], mul_out[6], mul_out[5], mul_out[4], mul_out[3], mul_out[2], mul_out[1], mul_out[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, mul_a, mul_b, mul_en_in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
