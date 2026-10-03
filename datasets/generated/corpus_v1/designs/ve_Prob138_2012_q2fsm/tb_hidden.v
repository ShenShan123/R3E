`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg w;
  wire z;
  TopModule dut(.clk(clk), .reset(reset), .w(w), .z(z));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,z");
    w = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      w = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, z);
    end
    $fclose(f);
    $finish;
  end
endmodule
