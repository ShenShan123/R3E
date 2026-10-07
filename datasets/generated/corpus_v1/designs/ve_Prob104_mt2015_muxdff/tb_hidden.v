`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg L;
  reg q_in;
  reg r_in;
  wire Q;
  TopModule dut(.clk(clk), .L(L), .q_in(q_in), .r_in(r_in), .Q(Q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,Q");
    L = 0;
    q_in = 0;
    r_in = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      L = $random(s);
      q_in = $random(s);
      r_in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, Q);
    end
    $fclose(f);
    $finish;
  end
endmodule
