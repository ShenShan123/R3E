`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg i_en;
  reg [63:0] adda;
  reg [63:0] addb;
  wire [64:0] result;
  wire o_en;
  verified_adder_64bit dut(.clk(clk), .rst_n(rst_n), .i_en(i_en), .adda(adda), .addb(addb), .result(result), .o_en(o_en));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,result[64],result[63],result[62],result[61],result[60],result[59],result[58],result[57],result[56],result[55],result[54],result[53],result[52],result[51],result[50],result[49],result[48],result[47],result[46],result[45],result[44],result[43],result[42],result[41],result[40],result[39],result[38],result[37],result[36],result[35],result[34],result[33],result[32],result[31],result[30],result[29],result[28],result[27],result[26],result[25],result[24],result[23],result[22],result[21],result[20],result[19],result[18],result[17],result[16],result[15],result[14],result[13],result[12],result[11],result[10],result[9],result[8],result[7],result[6],result[5],result[4],result[3],result[2],result[1],result[0],o_en");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,i_en,adda,addb");
    i_en = 0;
    adda = 0;
    addb = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      i_en = $random(s);
      adda = {$random(s), $random(s)};
      addb = {$random(s), $random(s)};
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, result[64], result[63], result[62], result[61], result[60], result[59], result[58], result[57], result[56], result[55], result[54], result[53], result[52], result[51], result[50], result[49], result[48], result[47], result[46], result[45], result[44], result[43], result[42], result[41], result[40], result[39], result[38], result[37], result[36], result[35], result[34], result[33], result[32], result[31], result[30], result[29], result[28], result[27], result[26], result[25], result[24], result[23], result[22], result[21], result[20], result[19], result[18], result[17], result[16], result[15], result[14], result[13], result[12], result[11], result[10], result[9], result[8], result[7], result[6], result[5], result[4], result[3], result[2], result[1], result[0], o_en);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, i_en, adda, addb);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
