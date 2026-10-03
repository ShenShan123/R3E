`timescale 1ns/1ps
module r3e_tb;
  reg clock = 0;
  reg a;
  wire p;
  wire q;
  TopModule dut(.clock(clock), .a(a), .p(p), .q(q));
  integer f, i, s, r3e_stim;
  always #5 clock = ~clock;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,p,q");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a");
    a = 0;
    repeat (2) @(negedge clock);
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      @(negedge clock);
      $fdisplay(f, "%0d,%b,%b", i, p, q);
      $fdisplay(r3e_stim, "%0d,%b", i, a);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
