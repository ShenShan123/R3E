`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg a;
  reg b;
  wire q;
  wire state;
  TopModule dut(.clk(clk), .a(a), .b(b), .q(q), .state(state));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q,state");
    a = 0;
    b = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", i, q, state);
    end
    $fclose(f);
    $finish;
  end
endmodule
