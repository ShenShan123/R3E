`timescale 1ns/1ps
module r3e_tb;
  reg in1;
  reg in2;
  wire out;
  TopModule dut(.in1(in1), .in2(in2), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out");
    in1 = 0;
    in2 = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in1 = $random(s);
      in2 = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", $time, out);
    end
    $fclose(f);
    $finish;
  end
endmodule
