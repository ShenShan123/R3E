`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg in;
  wire out;
  TopModule dut(.clk(clk), .in(in), .out(out));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in");
    in = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, out);
      $fdisplay(r3e_stim, "%0d,%b", i, in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
