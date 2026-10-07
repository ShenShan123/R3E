`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg a;
  reg b;
  wire out_assign;
  wire out_always_comb;
  wire out_always_ff;
  TopModule dut(.clk(clk), .a(a), .b(b), .out_assign(out_assign), .out_always_comb(out_always_comb), .out_always_ff(out_always_ff));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out_assign,out_always_comb,out_always_ff");
    a = 0;
    b = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", $time, out_assign, out_always_comb, out_always_ff);
    end
    $fclose(f);
    $finish;
  end
endmodule
