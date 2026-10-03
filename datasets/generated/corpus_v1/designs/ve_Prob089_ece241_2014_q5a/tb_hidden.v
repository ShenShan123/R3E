`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg x;
  wire z;
  TopModule dut(.clk(clk), .areset(areset), .x(x), .z(z));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,z");
    x = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      x = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, z);
    end
    $fclose(f);
    $finish;
  end
endmodule
