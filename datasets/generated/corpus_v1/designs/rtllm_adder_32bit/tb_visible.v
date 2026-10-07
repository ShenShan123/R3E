`timescale 1ns/1ps
module r3e_tb;
  reg [31:0] A;
  reg [31:0] B;
  wire [31:0] S;
  wire C32;
  verified_adder_32bit dut(.A(A), .B(B), .S(S), .C32(C32));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,S[31],S[30],S[29],S[28],S[27],S[26],S[25],S[24],S[23],S[22],S[21],S[20],S[19],S[18],S[17],S[16],S[15],S[14],S[13],S[12],S[11],S[10],S[9],S[8],S[7],S[6],S[5],S[4],S[3],S[2],S[1],S[0],C32");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,A,B");
    A = 0;
    B = 0;
    for (i = 0; i < 64; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, S[31], S[30], S[29], S[28], S[27], S[26], S[25], S[24], S[23], S[22], S[21], S[20], S[19], S[18], S[17], S[16], S[15], S[14], S[13], S[12], S[11], S[10], S[9], S[8], S[7], S[6], S[5], S[4], S[3], S[2], S[1], S[0], C32);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, A, B);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
