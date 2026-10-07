`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst;
  wire [3:0] out;
  LFSR dut(.out(out), .clk(clk), .rst(rst));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0]");
    rst = 1;
    repeat (2) @(negedge clk);
    rst = 0;
    for (i = 0; i < 160; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, out[3], out[2], out[1], out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
