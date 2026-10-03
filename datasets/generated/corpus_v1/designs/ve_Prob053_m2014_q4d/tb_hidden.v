`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg in;
  wire out;
  TopModule dut(.clk(clk), .in(in), .out(out));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out");
    in = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, out);
    end
    $fclose(f);
    $finish;
  end
endmodule
