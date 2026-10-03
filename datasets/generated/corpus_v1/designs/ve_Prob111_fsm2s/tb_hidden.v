`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg j;
  reg k;
  wire out;
  TopModule dut(.clk(clk), .j(j), .k(k), .reset(reset), .out(out));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out");
    j = 0;
    k = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      j = $random(s);
      k = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, out);
    end
    $fclose(f);
    $finish;
  end
endmodule
