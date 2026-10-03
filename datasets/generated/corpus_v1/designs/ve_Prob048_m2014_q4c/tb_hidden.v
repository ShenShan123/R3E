`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg d;
  reg r;
  wire q;
  TopModule dut(.clk(clk), .d(d), .r(r), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q");
    d = 0;
    r = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      d = $random(s);
      r = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, q);
    end
    $fclose(f);
    $finish;
  end
endmodule
