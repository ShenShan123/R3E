`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg j;
  reg k;
  wire Q;
  TopModule dut(.clk(clk), .j(j), .k(k), .Q(Q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,Q");
    j = 0;
    k = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      j = $random(s);
      k = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, Q);
    end
    $fclose(f);
    $finish;
  end
endmodule
