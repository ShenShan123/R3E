`timescale 1ns/1ps
module r3e_tb;
  reg [2:0] a;
  reg [2:0] b;
  wire [2:0] out_or_bitwise;
  wire out_or_logical;
  wire [5:0] out_not;
  TopModule dut(.a(a), .b(b), .out_or_bitwise(out_or_bitwise), .out_or_logical(out_or_logical), .out_not(out_not));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out_or_bitwise[2],out_or_bitwise[1],out_or_bitwise[0],out_or_logical,out_not[5],out_not[4],out_not[3],out_not[2],out_not[1],out_not[0]");
    a = 0;
    b = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, out_or_bitwise[2], out_or_bitwise[1], out_or_bitwise[0], out_or_logical, out_not[5], out_not[4], out_not[3], out_not[2], out_not[1], out_not[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
