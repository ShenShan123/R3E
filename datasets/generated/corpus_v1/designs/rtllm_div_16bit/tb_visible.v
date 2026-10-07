`timescale 1ns/1ps
module r3e_tb;
  reg [15:0] A;
  reg [7:0] B;
  wire [15:0] result;
  wire [15:0] odd;
  verified_div_16bit dut(.A(A), .B(B), .result(result), .odd(odd));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,result[15],result[14],result[13],result[12],result[11],result[10],result[9],result[8],result[7],result[6],result[5],result[4],result[3],result[2],result[1],result[0],odd[15],odd[14],odd[13],odd[12],odd[11],odd[10],odd[9],odd[8],odd[7],odd[6],odd[5],odd[4],odd[3],odd[2],odd[1],odd[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,A,B");
    A = 0;
    B = 0;
    for (i = 0; i < 64; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, result[15], result[14], result[13], result[12], result[11], result[10], result[9], result[8], result[7], result[6], result[5], result[4], result[3], result[2], result[1], result[0], odd[15], odd[14], odd[13], odd[12], odd[11], odd[10], odd[9], odd[8], odd[7], odd[6], odd[5], odd[4], odd[3], odd[2], odd[1], odd[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, A, B);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
