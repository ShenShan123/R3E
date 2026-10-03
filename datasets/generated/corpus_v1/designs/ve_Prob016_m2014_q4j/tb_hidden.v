`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] x;
  reg [3:0] y;
  wire [4:0] sum;
  TopModule dut(.x(x), .y(y), .sum(sum));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,sum[4],sum[3],sum[2],sum[1],sum[0]");
    x = 0;
    y = 0;
    for (i = 0; i < 160; i = i + 1) begin
      x = $random(s);
      y = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", i, sum[4], sum[3], sum[2], sum[1], sum[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
