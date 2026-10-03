`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  wire [4:0] wave;
  verified_signal_generator dut(.clk(clk), .rst_n(rst_n), .wave(wave));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,wave[4],wave[3],wave[2],wave[1],wave[0]");
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", i, wave[4], wave[3], wave[2], wave[1], wave[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
