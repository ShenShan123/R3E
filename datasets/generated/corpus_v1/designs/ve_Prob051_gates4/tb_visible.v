`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] in;
  wire out_and;
  wire out_or;
  wire out_xor;
  TopModule dut(.in(in), .out_and(out_and), .out_or(out_or), .out_xor(out_xor));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out_and,out_or,out_xor");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in");
    in = 0;
    for (i = 0; i < 64; i = i + 1) begin
      in = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", $time, out_and, out_or, out_xor);
      $fdisplay(r3e_stim, "%0d,%b", $time, in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
