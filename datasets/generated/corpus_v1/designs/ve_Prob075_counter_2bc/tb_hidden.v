`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg train_valid;
  reg train_taken;
  wire [1:0] state;
  TopModule dut(.clk(clk), .areset(areset), .train_valid(train_valid), .train_taken(train_taken), .state(state));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,state[1],state[0]");
    train_valid = 0;
    train_taken = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      train_valid = $random(s);
      train_taken = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", i, state[1], state[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
