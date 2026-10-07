`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  wire [63:0] Q;
  verified_JC_counter dut(.clk(clk), .rst_n(rst_n), .Q(Q));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,Q[63],Q[62],Q[61],Q[60],Q[59],Q[58],Q[57],Q[56],Q[55],Q[54],Q[53],Q[52],Q[51],Q[50],Q[49],Q[48],Q[47],Q[46],Q[45],Q[44],Q[43],Q[42],Q[41],Q[40],Q[39],Q[38],Q[37],Q[36],Q[35],Q[34],Q[33],Q[32],Q[31],Q[30],Q[29],Q[28],Q[27],Q[26],Q[25],Q[24],Q[23],Q[22],Q[21],Q[20],Q[19],Q[18],Q[17],Q[16],Q[15],Q[14],Q[13],Q[12],Q[11],Q[10],Q[9],Q[8],Q[7],Q[6],Q[5],Q[4],Q[3],Q[2],Q[1],Q[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,");
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, Q[63], Q[62], Q[61], Q[60], Q[59], Q[58], Q[57], Q[56], Q[55], Q[54], Q[53], Q[52], Q[51], Q[50], Q[49], Q[48], Q[47], Q[46], Q[45], Q[44], Q[43], Q[42], Q[41], Q[40], Q[39], Q[38], Q[37], Q[36], Q[35], Q[34], Q[33], Q[32], Q[31], Q[30], Q[29], Q[28], Q[27], Q[26], Q[25], Q[24], Q[23], Q[22], Q[21], Q[20], Q[19], Q[18], Q[17], Q[16], Q[15], Q[14], Q[13], Q[12], Q[11], Q[10], Q[9], Q[8], Q[7], Q[6], Q[5], Q[4], Q[3], Q[2], Q[1], Q[0]);
      $fdisplay(r3e_stim, "%0d", $time);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
