`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  reg c;
  reg d;
  wire out;
  wire out_n;
  TopModule dut(.a(a), .b(b), .c(c), .d(d), .out(out), .out_n(out_n));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out,out_n");
    a = 0;
    b = 0;
    c = 0;
    d = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      c = $random(s);
      d = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, out, out_n);
    end
    $fclose(f);
    $finish;
  end
endmodule
