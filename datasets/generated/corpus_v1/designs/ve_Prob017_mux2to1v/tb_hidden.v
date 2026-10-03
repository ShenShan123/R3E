`timescale 1ns/1ps
module r3e_tb;
  reg [99:0] a;
  reg [99:0] b;
  reg sel;
  wire [99:0] out;
  TopModule dut(.a(a), .b(b), .sel(sel), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[99],out[98],out[97],out[96],out[95],out[94],out[93],out[92],out[91],out[90],out[89],out[88],out[87],out[86],out[85],out[84],out[83],out[82],out[81],out[80],out[79],out[78],out[77],out[76],out[75],out[74],out[73],out[72],out[71],out[70],out[69],out[68],out[67],out[66],out[65],out[64],out[63],out[62],out[61],out[60],out[59],out[58],out[57],out[56],out[55],out[54],out[53],out[52],out[51],out[50],out[49],out[48],out[47],out[46],out[45],out[44],out[43],out[42],out[41],out[40],out[39],out[38],out[37],out[36],out[35],out[34],out[33],out[32],out[31],out[30],out[29],out[28],out[27],out[26],out[25],out[24],out[23],out[22],out[21],out[20],out[19],out[18],out[17],out[16],out[15],out[14],out[13],out[12],out[11],out[10],out[9],out[8],out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0]");
    a = 0;
    b = 0;
    sel = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = {$random(s), $random(s), $random(s), $random(s)};
      b = {$random(s), $random(s), $random(s), $random(s)};
      sel = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, out[99], out[98], out[97], out[96], out[95], out[94], out[93], out[92], out[91], out[90], out[89], out[88], out[87], out[86], out[85], out[84], out[83], out[82], out[81], out[80], out[79], out[78], out[77], out[76], out[75], out[74], out[73], out[72], out[71], out[70], out[69], out[68], out[67], out[66], out[65], out[64], out[63], out[62], out[61], out[60], out[59], out[58], out[57], out[56], out[55], out[54], out[53], out[52], out[51], out[50], out[49], out[48], out[47], out[46], out[45], out[44], out[43], out[42], out[41], out[40], out[39], out[38], out[37], out[36], out[35], out[34], out[33], out[32], out[31], out[30], out[29], out[28], out[27], out[26], out[25], out[24], out[23], out[22], out[21], out[20], out[19], out[18], out[17], out[16], out[15], out[14], out[13], out[12], out[11], out[10], out[9], out[8], out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
