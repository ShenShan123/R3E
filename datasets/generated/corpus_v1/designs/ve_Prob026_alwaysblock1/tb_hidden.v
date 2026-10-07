`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  wire out_assign;
  wire out_alwaysblock;
  TopModule dut(.a(a), .b(b), .out_assign(out_assign), .out_alwaysblock(out_alwaysblock));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out_assign,out_alwaysblock");
    a = 0;
    b = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", $time, out_assign, out_alwaysblock);
    end
    $fclose(f);
    $finish;
  end
endmodule
