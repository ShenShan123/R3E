`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] A;
  reg [3:0] B;
  reg Cin;
  wire [3:0] Sum;
  wire Cout;
  adder_bcd dut(.A(A), .B(B), .Cin(Cin), .Sum(Sum), .Cout(Cout));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,Sum[3],Sum[2],Sum[1],Sum[0],Cout");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,A,B,Cin");
    A = 0;
    B = 0;
    Cin = 0;
    for (i = 0; i < 64; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      Cin = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", i, Sum[3], Sum[2], Sum[1], Sum[0], Cout);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", i, A, B, Cin);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
