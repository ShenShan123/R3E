`timescale 1ns/1ps
module r3e_tb;
  reg [99:0] in;
  wire out_and;
  wire out_or;
  wire out_xor;
  TopModule dut(.in(in), .out_and(out_and), .out_or(out_or), .out_xor(out_xor));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out_and,out_or,out_xor");
    in = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = {$random(s), $random(s), $random(s), $random(s)};
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", $time, out_and, out_or, out_xor);
    end
    $fclose(f);
    $finish;
  end
endmodule
