`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst;
  reg [7:0] dividend;
  reg [7:0] divisor;
  reg sign;
  reg opn_valid;
  reg res_ready;
  wire res_valid;
  wire [15:0] result;
  radix2_div dut(.clk(clk), .rst(rst), .dividend(dividend), .divisor(divisor), .sign(sign), .opn_valid(opn_valid), .res_valid(res_valid), .res_ready(res_ready), .result(result));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,res_valid,result[15],result[14],result[13],result[12],result[11],result[10],result[9],result[8],result[7],result[6],result[5],result[4],result[3],result[2],result[1],result[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,dividend,divisor,sign,opn_valid,res_ready");
    dividend = 0;
    divisor = 0;
    sign = 0;
    opn_valid = 0;
    res_ready = 0;
    rst = 1;
    repeat (2) @(negedge clk);
    rst = 0;
    for (i = 0; i < 64; i = i + 1) begin
      dividend = $random(s);
      divisor = $random(s);
      sign = $random(s);
      opn_valid = $random(s);
      res_ready = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, res_valid, result[15], result[14], result[13], result[12], result[11], result[10], result[9], result[8], result[7], result[6], result[5], result[4], result[3], result[2], result[1], result[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b,%b", i, dividend, divisor, sign, opn_valid, res_ready);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
