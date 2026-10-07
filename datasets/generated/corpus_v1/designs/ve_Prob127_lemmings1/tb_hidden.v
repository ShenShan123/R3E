`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg bump_left;
  reg bump_right;
  wire walk_left;
  wire walk_right;
  TopModule dut(.clk(clk), .areset(areset), .bump_left(bump_left), .bump_right(bump_right), .walk_left(walk_left), .walk_right(walk_right));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,walk_left,walk_right");
    bump_left = 0;
    bump_right = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      bump_left = $random(s);
      bump_right = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", $time, walk_left, walk_right);
    end
    $fclose(f);
    $finish;
  end
endmodule
