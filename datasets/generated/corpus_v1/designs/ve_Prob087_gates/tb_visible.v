`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  wire out_and;
  wire out_or;
  wire out_xor;
  wire out_nand;
  wire out_nor;
  wire out_xnor;
  wire out_anotb;
  TopModule dut(.a(a), .b(b), .out_and(out_and), .out_or(out_or), .out_xor(out_xor), .out_nand(out_nand), .out_nor(out_nor), .out_xnor(out_xnor), .out_anotb(out_anotb));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out_and,out_or,out_xor,out_nand,out_nor,out_xnor,out_anotb");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b");
    a = 0;
    b = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b", i, out_and, out_or, out_xor, out_nand, out_nor, out_xnor, out_anotb);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
