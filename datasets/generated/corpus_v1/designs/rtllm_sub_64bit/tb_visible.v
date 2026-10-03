`timescale 1ns/1ps
module r3e_tb;
  reg [63:0] A;
  reg [63:0] B;
  wire [63:0] result;
  wire overflow;
  sub_64bit dut(.A(A), .B(B), .result(result), .overflow(overflow));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,result[63],result[62],result[61],result[60],result[59],result[58],result[57],result[56],result[55],result[54],result[53],result[52],result[51],result[50],result[49],result[48],result[47],result[46],result[45],result[44],result[43],result[42],result[41],result[40],result[39],result[38],result[37],result[36],result[35],result[34],result[33],result[32],result[31],result[30],result[29],result[28],result[27],result[26],result[25],result[24],result[23],result[22],result[21],result[20],result[19],result[18],result[17],result[16],result[15],result[14],result[13],result[12],result[11],result[10],result[9],result[8],result[7],result[6],result[5],result[4],result[3],result[2],result[1],result[0],overflow");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,A,B");
    A = 0;
    B = 0;
    for (i = 0; i < 64; i = i + 1) begin
      A = {$random(s), $random(s)};
      B = {$random(s), $random(s)};
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, result[63], result[62], result[61], result[60], result[59], result[58], result[57], result[56], result[55], result[54], result[53], result[52], result[51], result[50], result[49], result[48], result[47], result[46], result[45], result[44], result[43], result[42], result[41], result[40], result[39], result[38], result[37], result[36], result[35], result[34], result[33], result[32], result[31], result[30], result[29], result[28], result[27], result[26], result[25], result[24], result[23], result[22], result[21], result[20], result[19], result[18], result[17], result[16], result[15], result[14], result[13], result[12], result[11], result[10], result[9], result[8], result[7], result[6], result[5], result[4], result[3], result[2], result[1], result[0], overflow);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, A, B);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
