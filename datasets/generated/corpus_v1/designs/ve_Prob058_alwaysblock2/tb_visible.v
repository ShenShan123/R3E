`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg a;
  reg b;
  wire out_assign;
  wire out_always_comb;
  wire out_always_ff;
  TopModule dut(.clk(clk), .a(a), .b(b), .out_assign(out_assign), .out_always_comb(out_always_comb), .out_always_ff(out_always_ff));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out_assign,out_always_comb,out_always_ff");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b");
    a = 0;
    b = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", i, out_assign, out_always_comb, out_always_ff);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
