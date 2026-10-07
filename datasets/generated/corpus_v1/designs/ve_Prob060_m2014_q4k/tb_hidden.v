`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg resetn;
  reg in;
  wire out;
  TopModule dut(.clk(clk), .resetn(resetn), .in(in), .out(out));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out");
    in = 0;
    resetn = 0;
    repeat (2) @(negedge clk);
    resetn = 1;
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, out);
    end
    $fclose(f);
    $finish;
  end
endmodule
