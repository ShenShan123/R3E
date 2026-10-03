`timescale 1ns/1ps
module r3e_tb;
  reg do_sub;
  reg [7:0] a;
  reg [7:0] b;
  wire [7:0] out;
  wire result_is_zero;
  TopModule dut(.do_sub(do_sub), .a(a), .b(b), .out(out), .result_is_zero(result_is_zero));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0],result_is_zero");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,do_sub,a,b");
    do_sub = 0;
    a = 0;
    b = 0;
    for (i = 0; i < 64; i = i + 1) begin
      do_sub = $random(s);
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0], result_is_zero);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", i, do_sub, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
