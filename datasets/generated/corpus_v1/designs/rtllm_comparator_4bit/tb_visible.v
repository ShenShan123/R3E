`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] A;
  reg [3:0] B;
  wire A_greater;
  wire A_equal;
  wire A_less;
  comparator_4bit dut(.A(A), .B(B), .A_greater(A_greater), .A_equal(A_equal), .A_less(A_less));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,A_greater,A_equal,A_less");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,A,B");
    A = 0;
    B = 0;
    for (i = 0; i < 64; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", $time, A_greater, A_equal, A_less);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, A, B);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
