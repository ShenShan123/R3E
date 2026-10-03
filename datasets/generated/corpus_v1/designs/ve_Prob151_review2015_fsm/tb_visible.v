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
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,shift_ena,counting,done");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset reset held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,data,done_counting,ack");
    data = 0;
    done_counting = 0;
    ack = 0;
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 64; i = i + 1) begin
      data = $random(s);
      done_counting = $random(s);
      ack = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b", i, shift_ena, counting, done);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", i, data, done_counting, ack);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
