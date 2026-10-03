`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  reg sel_b1;
  reg sel_b2;
  wire out_assign;
  wire out_always;
  TopModule dut(.a(a), .b(b), .sel_b1(sel_b1), .sel_b2(sel_b2), .out_assign(out_assign), .out_always(out_always));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out_assign,out_always");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b,sel_b1,sel_b2");
    a = 0;
    b = 0;
    sel_b1 = 0;
    sel_b2 = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      sel_b1 = $random(s);
      sel_b2 = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, out_assign, out_always);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b", i, a, b, sel_b1, sel_b2);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
