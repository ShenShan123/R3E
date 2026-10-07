`timescale 1ns/1ps
module r3e_tb;
  reg [1:0] A;
  reg [1:0] B;
  wire z;
  TopModule dut(.A(A), .B(B), .z(z));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,z");
    A = 0;
    B = 0;
    for (i = 0; i < 160; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", $time, z);
    end
    $fclose(f);
    $finish;
  end
endmodule
