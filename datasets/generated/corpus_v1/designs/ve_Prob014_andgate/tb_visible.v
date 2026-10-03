`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  wire out;
  TopModule dut(.a(a), .b(b), .out(out));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b");
    a = 0;
    b = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", i, out);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
