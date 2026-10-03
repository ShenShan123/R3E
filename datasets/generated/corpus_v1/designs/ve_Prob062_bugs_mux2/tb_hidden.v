`timescale 1ns/1ps
module r3e_tb;
  reg sel;
  reg [7:0] a;
  reg [7:0] b;
  wire [7:0] out;
  TopModule dut(.sel(sel), .a(a), .b(b), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0]");
    sel = 0;
    a = 0;
    b = 0;
    for (i = 0; i < 160; i = i + 1) begin
      sel = $random(s);
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", i, out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
