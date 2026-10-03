`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst;
  reg [31:0] a;
  reg [31:0] b;
  wire [31:0] c;
  verified_pe dut(.clk(clk), .rst(rst), .a(a), .b(b), .c(c));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,c[31],c[30],c[29],c[28],c[27],c[26],c[25],c[24],c[23],c[22],c[21],c[20],c[19],c[18],c[17],c[16],c[15],c[14],c[13],c[12],c[11],c[10],c[9],c[8],c[7],c[6],c[5],c[4],c[3],c[2],c[1],c[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,a,b");
    a = 0;
    b = 0;
    rst = 1;
    repeat (2) @(negedge clk);
    rst = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, c[31], c[30], c[29], c[28], c[27], c[26], c[25], c[24], c[23], c[22], c[21], c[20], c[19], c[18], c[17], c[16], c[15], c[14], c[13], c[12], c[11], c[10], c[9], c[8], c[7], c[6], c[5], c[4], c[3], c[2], c[1], c[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
