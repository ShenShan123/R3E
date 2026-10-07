`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg L;
  reg q_in;
  reg r_in;
  wire Q;
  TopModule dut(.clk(clk), .L(L), .q_in(q_in), .r_in(r_in), .Q(Q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,Q");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,L,q_in,r_in");
    L = 0;
    q_in = 0;
    r_in = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      L = $random(s);
      q_in = $random(s);
      r_in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, Q);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, L, q_in, r_in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
