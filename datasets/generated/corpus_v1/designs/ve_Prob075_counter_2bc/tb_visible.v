`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg train_valid;
  reg train_taken;
  wire [1:0] state;
  TopModule dut(.clk(clk), .areset(areset), .train_valid(train_valid), .train_taken(train_taken), .state(state));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,state[1],state[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset areset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,train_valid,train_taken");
    train_valid = 0;
    train_taken = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      train_valid = $random(s);
      train_taken = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", $time, state[1], state[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, train_valid, train_taken);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
