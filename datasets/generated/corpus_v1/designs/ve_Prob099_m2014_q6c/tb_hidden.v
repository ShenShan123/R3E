`timescale 1ns/1ps
module r3e_tb;
  reg [5:0] y;
  reg w;
  wire Y1;
  wire Y3;
  TopModule dut(.y(y), .w(w), .Y1(Y1), .Y3(Y3));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,Y1,Y3");
    y = 0;
    w = 0;
    for (i = 0; i < 160; i = i + 1) begin
      y = $random(s);
      w = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, Y1, Y3);
    end
    $fclose(f);
    $finish;
  end
endmodule
