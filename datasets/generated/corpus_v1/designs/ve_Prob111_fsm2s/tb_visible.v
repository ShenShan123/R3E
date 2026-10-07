`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg j;
  reg k;
  wire out;
  TopModule dut(.clk(clk), .j(j), .k(k), .reset(reset), .out(out));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset reset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,j,k");
    j = 0;
    k = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      j = $random(s);
      k = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", $time, out);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, j, k);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
