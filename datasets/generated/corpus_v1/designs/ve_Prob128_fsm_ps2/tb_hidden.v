`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg [7:0] in;
  wire done;
  TopModule dut(.clk(clk), .in(in), .reset(reset), .done(done));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,done");
    in = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, done);
    end
    $fclose(f);
    $finish;
  end
endmodule
