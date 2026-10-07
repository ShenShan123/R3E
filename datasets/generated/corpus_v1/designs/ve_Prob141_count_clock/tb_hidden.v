`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg ena;
  wire pm;
  wire [7:0] hh;
  wire [7:0] mm;
  wire [7:0] ss;
  TopModule dut(.clk(clk), .reset(reset), .ena(ena), .pm(pm), .hh(hh), .mm(mm), .ss(ss));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,pm,hh[7],hh[6],hh[5],hh[4],hh[3],hh[2],hh[1],hh[0],mm[7],mm[6],mm[5],mm[4],mm[3],mm[2],mm[1],mm[0],ss[7],ss[6],ss[5],ss[4],ss[3],ss[2],ss[1],ss[0]");
    ena = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      ena = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, pm, hh[7], hh[6], hh[5], hh[4], hh[3], hh[2], hh[1], hh[0], mm[7], mm[6], mm[5], mm[4], mm[3], mm[2], mm[1], mm[0], ss[7], ss[6], ss[5], ss[4], ss[3], ss[2], ss[1], ss[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
