`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg bump_left;
  reg bump_right;
  reg ground;
  wire walk_left;
  wire walk_right;
  wire aaah;
  TopModule dut(.clk(clk), .areset(areset), .bump_left(bump_left), .bump_right(bump_right), .ground(ground), .walk_left(walk_left), .walk_right(walk_right), .aaah(aaah));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,walk_left,walk_right,aaah");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset areset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,bump_left,bump_right,ground");
    bump_left = 0;
    bump_right = 0;
    ground = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      bump_left = $random(s);
      bump_right = $random(s);
      ground = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", $time, walk_left, walk_right, aaah);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, bump_left, bump_right, ground);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
