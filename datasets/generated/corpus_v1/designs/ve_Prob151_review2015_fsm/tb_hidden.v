`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  reg data;
  reg done_counting;
  reg ack;
  wire shift_ena;
  wire counting;
  wire done;
  TopModule dut(.clk(clk), .reset(reset), .data(data), .shift_ena(shift_ena), .counting(counting), .done_counting(done_counting), .done(done), .ack(ack));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,shift_ena,counting,done");
    data = 0;
    done_counting = 0;
    ack = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      data = $random(s);
      done_counting = $random(s);
      ack = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", $time, shift_ena, counting, done);
    end
    $fclose(f);
    $finish;
  end
endmodule
