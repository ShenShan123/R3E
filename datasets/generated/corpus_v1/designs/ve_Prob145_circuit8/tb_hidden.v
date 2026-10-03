`timescale 1ns/1ps
module r3e_tb;
  reg clock = 0;
  reg a;
  wire p;
  wire q;
  TopModule dut(.clock(clock), .a(a), .p(p), .q(q));
  integer f, i, s;
  always #5 clock = ~clock;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,p,q");
    a = 0;
    repeat (2) @(negedge clock);
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      @(negedge clock);
      $fdisplay(f, "%0d,%b,%b", i, p, q);
    end
    $fclose(f);
    $finish;
  end
endmodule
