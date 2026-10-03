`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg j;
  reg k;
  wire Q;
  TopModule dut(.clk(clk), .j(j), .k(k), .Q(Q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,Q");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,j,k");
    j = 0;
    k = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      j = $random(s);
      k = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, Q);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, j, k);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
