`timescale 1ns/1ps
module r3e_tb;
  reg d;
  reg ena;
  wire q;
  TopModule dut(.d(d), .ena(ena), .q(q));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q");
    d = 0;
    ena = 0;
    for (i = 0; i < 160; i = i + 1) begin
      d = $random(s);
      ena = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", i, q);
    end
    $fclose(f);
    $finish;
  end
endmodule
