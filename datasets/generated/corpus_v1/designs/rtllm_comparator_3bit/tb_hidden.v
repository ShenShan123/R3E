`timescale 1ns/1ps
module r3e_tb;
  reg [2:0] A;
  reg [2:0] B;
  wire A_greater;
  wire A_equal;
  wire A_less;
  comparator_3bit dut(.A(A), .B(B), .A_greater(A_greater), .A_equal(A_equal), .A_less(A_less));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,A_greater,A_equal,A_less");
    A = 0;
    B = 0;
    for (i = 0; i < 160; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", $time, A_greater, A_equal, A_less);
    end
    $fclose(f);
    $finish;
  end
endmodule
