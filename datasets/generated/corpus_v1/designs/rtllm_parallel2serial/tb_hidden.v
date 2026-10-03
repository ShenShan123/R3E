`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg [3:0] d;
  wire valid_out;
  wire dout;
  verified_parallel2serial dut(.clk(clk), .rst_n(rst_n), .d(d), .valid_out(valid_out), .dout(dout));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,valid_out,dout");
    d = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      d = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b", i, valid_out, dout);
    end
    $fclose(f);
    $finish;
  end
endmodule
