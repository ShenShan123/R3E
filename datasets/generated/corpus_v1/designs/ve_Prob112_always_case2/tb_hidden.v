`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] in;
  wire [1:0] pos;
  TopModule dut(.in(in), .pos(pos));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,pos[1],pos[0]");
    in = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", $time, pos[1], pos[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
