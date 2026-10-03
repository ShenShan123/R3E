`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg [7:0] in;
  wire [7:0] pedge;
  TopModule dut(.clk(clk), .in(in), .pedge(pedge));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,pedge[7],pedge[6],pedge[5],pedge[4],pedge[3],pedge[2],pedge[1],pedge[0]");
    in = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", i, pedge[7], pedge[6], pedge[5], pedge[4], pedge[3], pedge[2], pedge[1], pedge[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
