`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg [7:0] in;
  wire [7:0] pedge;
  TopModule dut(.clk(clk), .in(in), .pedge(pedge));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,pedge[7],pedge[6],pedge[5],pedge[4],pedge[3],pedge[2],pedge[1],pedge[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in");
    in = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", $time, pedge[7], pedge[6], pedge[5], pedge[4], pedge[3], pedge[2], pedge[1], pedge[0]);
      $fdisplay(r3e_stim, "%0d,%b", $time, in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
