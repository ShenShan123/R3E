`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  wire [7:0] out;
  ring_counter dut(.clk(clk), .reset(reset), .out(out));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset reset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,");
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", $time, out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0]);
      $fdisplay(r3e_stim, "%0d", $time);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
