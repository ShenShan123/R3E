`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg load;
  reg [1:0] ena;
  reg [99:0] data;
  wire [99:0] q;
  TopModule dut(.clk(clk), .load(load), .ena(ena), .data(data), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[99],q[98],q[97],q[96],q[95],q[94],q[93],q[92],q[91],q[90],q[89],q[88],q[87],q[86],q[85],q[84],q[83],q[82],q[81],q[80],q[79],q[78],q[77],q[76],q[75],q[74],q[73],q[72],q[71],q[70],q[69],q[68],q[67],q[66],q[65],q[64],q[63],q[62],q[61],q[60],q[59],q[58],q[57],q[56],q[55],q[54],q[53],q[52],q[51],q[50],q[49],q[48],q[47],q[46],q[45],q[44],q[43],q[42],q[41],q[40],q[39],q[38],q[37],q[36],q[35],q[34],q[33],q[32],q[31],q[30],q[29],q[28],q[27],q[26],q[25],q[24],q[23],q[22],q[21],q[20],q[19],q[18],q[17],q[16],q[15],q[14],q[13],q[12],q[11],q[10],q[9],q[8],q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    load = 0;
    ena = 0;
    data = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      load = $random(s);
      ena = $random(s);
      data = {$random(s), $random(s), $random(s), $random(s)};
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, q[99], q[98], q[97], q[96], q[95], q[94], q[93], q[92], q[91], q[90], q[89], q[88], q[87], q[86], q[85], q[84], q[83], q[82], q[81], q[80], q[79], q[78], q[77], q[76], q[75], q[74], q[73], q[72], q[71], q[70], q[69], q[68], q[67], q[66], q[65], q[64], q[63], q[62], q[61], q[60], q[59], q[58], q[57], q[56], q[55], q[54], q[53], q[52], q[51], q[50], q[49], q[48], q[47], q[46], q[45], q[44], q[43], q[42], q[41], q[40], q[39], q[38], q[37], q[36], q[35], q[34], q[33], q[32], q[31], q[30], q[29], q[28], q[27], q[26], q[25], q[24], q[23], q[22], q[21], q[20], q[19], q[18], q[17], q[16], q[15], q[14], q[13], q[12], q[11], q[10], q[9], q[8], q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
