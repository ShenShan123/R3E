`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg data_in;
  wire sequence_detected;
  sequence_detector dut(.clk(clk), .rst_n(rst_n), .data_in(data_in), .sequence_detected(sequence_detected));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,sequence_detected");
    data_in = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      data_in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, sequence_detected);
    end
    $fclose(f);
    $finish;
  end
endmodule
