`timescale 1ns/1ps
module r3e_tb;
  reg [2:0] a;
  reg [2:0] b;
  wire [2:0] out_or_bitwise;
  wire out_or_logical;
  wire [5:0] out_not;
  TopModule dut(.a(a), .b(b), .out_or_bitwise(out_or_bitwise), .out_or_logical(out_or_logical), .out_not(out_not));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out_or_bitwise[2],out_or_bitwise[1],out_or_bitwise[0],out_or_logical,out_not[5],out_not[4],out_not[3],out_not[2],out_not[1],out_not[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b");
    a = 0;
    b = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, out_or_bitwise[2], out_or_bitwise[1], out_or_bitwise[0], out_or_logical, out_not[5], out_not[4], out_not[3], out_not[2], out_not[1], out_not[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
