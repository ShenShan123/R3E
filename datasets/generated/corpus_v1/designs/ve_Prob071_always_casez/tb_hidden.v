`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] in;
  wire [2:0] pos;
  TopModule dut(.in(in), .pos(pos));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,pos[2],pos[1],pos[0]");
    in = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", i, pos[2], pos[1], pos[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
