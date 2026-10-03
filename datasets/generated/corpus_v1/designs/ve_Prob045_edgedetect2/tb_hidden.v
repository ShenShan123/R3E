`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg [7:0] in;
  wire [7:0] anyedge;
  TopModule dut(.clk(clk), .in(in), .anyedge(anyedge));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,anyedge[7],anyedge[6],anyedge[5],anyedge[4],anyedge[3],anyedge[2],anyedge[1],anyedge[0]");
    in = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", i, anyedge[7], anyedge[6], anyedge[5], anyedge[4], anyedge[3], anyedge[2], anyedge[1], anyedge[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
