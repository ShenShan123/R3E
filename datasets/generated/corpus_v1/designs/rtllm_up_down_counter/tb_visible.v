`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg up_down;
  wire [15:0] count;
  up_down_counter dut(.clk(clk), .reset(reset), .up_down(up_down), .count(count));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,count[15],count[14],count[13],count[12],count[11],count[10],count[9],count[8],count[7],count[6],count[5],count[4],count[3],count[2],count[1],count[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset reset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,up_down");
    up_down = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      up_down = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, count[15], count[14], count[13], count[12], count[11], count[10], count[9], count[8], count[7], count[6], count[5], count[4], count[3], count[2], count[1], count[0]);
      $fdisplay(r3e_stim, "%0d,%b", $time, up_down);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
