`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg resetn;
  reg in;
  wire out;
  TopModule dut(.clk(clk), .resetn(resetn), .in(in), .out(out));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset resetn held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,in");
    in = 0;
    resetn = 0;
    repeat (2) @(negedge clk);
    resetn = 1;
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
