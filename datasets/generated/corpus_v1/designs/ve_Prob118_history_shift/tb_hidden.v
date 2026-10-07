`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg areset;
  reg predict_valid;
  reg predict_taken;
  reg train_mispredicted;
  reg train_taken;
  reg [31:0] train_history;
  wire [31:0] predict_history;
  TopModule dut(.clk(clk), .areset(areset), .predict_valid(predict_valid), .predict_taken(predict_taken), .predict_history(predict_history), .train_mispredicted(train_mispredicted), .train_taken(train_taken), .train_history(train_history));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,predict_history[31],predict_history[30],predict_history[29],predict_history[28],predict_history[27],predict_history[26],predict_history[25],predict_history[24],predict_history[23],predict_history[22],predict_history[21],predict_history[20],predict_history[19],predict_history[18],predict_history[17],predict_history[16],predict_history[15],predict_history[14],predict_history[13],predict_history[12],predict_history[11],predict_history[10],predict_history[9],predict_history[8],predict_history[7],predict_history[6],predict_history[5],predict_history[4],predict_history[3],predict_history[2],predict_history[1],predict_history[0]");
    predict_valid = 0;
    predict_taken = 0;
    train_mispredicted = 0;
    train_taken = 0;
    train_history = 0;
    areset = 1;
    repeat (2) @(negedge clk);
    areset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      predict_valid = $random(s);
      predict_taken = $random(s);
      train_mispredicted = $random(s);
      train_taken = $random(s);
      train_history = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, predict_history[31], predict_history[30], predict_history[29], predict_history[28], predict_history[27], predict_history[26], predict_history[25], predict_history[24], predict_history[23], predict_history[22], predict_history[21], predict_history[20], predict_history[19], predict_history[18], predict_history[17], predict_history[16], predict_history[15], predict_history[14], predict_history[13], predict_history[12], predict_history[11], predict_history[10], predict_history[9], predict_history[8], predict_history[7], predict_history[6], predict_history[5], predict_history[4], predict_history[3], predict_history[2], predict_history[1], predict_history[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
