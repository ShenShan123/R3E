`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  wire clk_div;
  freq_divbyfrac dut(.rst_n(rst_n), .clk(clk), .clk_div(clk_div));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,clk_div");
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, clk_div);
    end
    $fclose(f);
    $finish;
  end
endmodule
