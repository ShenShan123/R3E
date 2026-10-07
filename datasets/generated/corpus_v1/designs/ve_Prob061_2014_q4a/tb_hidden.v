`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg w;
  reg R;
  reg E;
  reg L;
  wire Q;
  TopModule dut(.clk(clk), .w(w), .R(R), .E(E), .L(L), .Q(Q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,Q");
    w = 0;
    R = 0;
    E = 0;
    L = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      w = $random(s);
      R = $random(s);
      E = $random(s);
      L = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, Q);
    end
    $fclose(f);
    $finish;
  end
endmodule
