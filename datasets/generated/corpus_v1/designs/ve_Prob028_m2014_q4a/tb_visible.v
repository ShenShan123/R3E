`timescale 1ns/1ps
module r3e_tb;
  reg d;
  reg ena;
  wire q;
  TopModule dut(.d(d), .ena(ena), .q(q));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,d,ena");
    d = 0;
    ena = 0;
    for (i = 0; i < 64; i = i + 1) begin
      d = $random(s);
      ena = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", i, q);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, d, ena);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
