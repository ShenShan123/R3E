`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg x;
  wire z;
  TopModule dut(.clk(clk), .x(x), .z(z));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,z");
    x = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      x = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, z);
    end
    $fclose(f);
    $finish;
  end
endmodule
