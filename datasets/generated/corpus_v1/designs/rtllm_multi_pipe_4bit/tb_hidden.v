`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg [3:0] mul_a;
  reg [3:0] mul_b;
  wire [7:0] mul_out;
  verified_multi_pipe dut(.clk(clk), .rst_n(rst_n), .mul_a(mul_a), .mul_b(mul_b), .mul_out(mul_out));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,mul_out[7],mul_out[6],mul_out[5],mul_out[4],mul_out[3],mul_out[2],mul_out[1],mul_out[0]");
    mul_a = 0;
    mul_b = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      mul_a = $random(s);
      mul_b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", $time, mul_out[7], mul_out[6], mul_out[5], mul_out[4], mul_out[3], mul_out[2], mul_out[1], mul_out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
