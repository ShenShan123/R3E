`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  wire q;
  TopModule dut(.a(a), .b(b), .q(q));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q");
    a = 0;
    b = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", i, q);
    end
    $fclose(f);
    $finish;
  end
endmodule
