`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg [7:0] a;
  reg [7:0] b;
  wire [15:0] p;
  wire rdy;
  verified_multi_booth_8bit dut(.p(p), .rdy(rdy), .clk(clk), .reset(reset), .a(a), .b(b));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,p[15],p[14],p[13],p[12],p[11],p[10],p[9],p[8],p[7],p[6],p[5],p[4],p[3],p[2],p[1],p[0],rdy");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset reset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,a,b");
    a = 0;
    b = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, p[15], p[14], p[13], p[12], p[11], p[10], p[9], p[8], p[7], p[6], p[5], p[4], p[3], p[2], p[1], p[0], rdy);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
