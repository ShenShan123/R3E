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
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,res_valid,result[15],result[14],result[13],result[12],result[11],result[10],result[9],result[8],result[7],result[6],result[5],result[4],result[3],result[2],result[1],result[0]");
    dividend = 0;
    divisor = 0;
    sign = 0;
    opn_valid = 0;
    res_ready = 0;
    rst = 1;
    repeat (2) @(negedge clk);
    rst = 0;
    for (i = 0; i < 160; i = i + 1) begin
      dividend = $random(s);
      divisor = $random(s);
      sign = $random(s);
      opn_valid = $random(s);
      res_ready = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, res_valid, result[15], result[14], result[13], result[12], result[11], result[10], result[9], result[8], result[7], result[6], result[5], result[4], result[3], result[2], result[1], result[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
