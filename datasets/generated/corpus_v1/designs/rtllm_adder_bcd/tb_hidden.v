`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] A;
  reg [3:0] B;
  reg Cin;
  wire [3:0] Sum;
  wire Cout;
  adder_bcd dut(.A(A), .B(B), .Cin(Cin), .Sum(Sum), .Cout(Cout));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,Sum[3],Sum[2],Sum[1],Sum[0],Cout");
    A = 0;
    B = 0;
    Cin = 0;
    for (i = 0; i < 160; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      Cin = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", $time, Sum[3], Sum[2], Sum[1], Sum[0], Cout);
    end
    $fclose(f);
    $finish;
  end
endmodule
