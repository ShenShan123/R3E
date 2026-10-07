`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg valid_count;
  wire [3:0] out;
  verified_counter_12 dut(.rst_n(rst_n), .clk(clk), .valid_count(valid_count), .out(out));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0]");
    valid_count = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      valid_count = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, out[3], out[2], out[1], out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
