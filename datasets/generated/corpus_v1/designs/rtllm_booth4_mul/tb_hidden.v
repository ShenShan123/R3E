`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg [7:0] a;
  reg [7:0] b;
  wire [15:0] p;
  wire rdy;
  verified_multi_booth_8bit dut(.p(p), .rdy(rdy), .clk(clk), .reset(reset), .a(a), .b(b));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,p[15],p[14],p[13],p[12],p[11],p[10],p[9],p[8],p[7],p[6],p[5],p[4],p[3],p[2],p[1],p[0],rdy");
    a = 0;
    b = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, p[15], p[14], p[13], p[12], p[11], p[10], p[9], p[8], p[7], p[6], p[5], p[4], p[3], p[2], p[1], p[0], rdy);
    end
    $fclose(f);
    $finish;
  end
endmodule
