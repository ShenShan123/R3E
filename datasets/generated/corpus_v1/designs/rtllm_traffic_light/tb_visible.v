`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg pass_request;
  wire [7:0] clock;
  wire red;
  wire yellow;
  wire green;
  verified_traffic_light dut(.rst_n(rst_n), .clk(clk), .pass_request(pass_request), .clock(clock), .red(red), .yellow(yellow), .green(green));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,clock[7],clock[6],clock[5],clock[4],clock[3],clock[2],clock[1],clock[0],red,yellow,green");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset rst_n held at 0 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,pass_request");
    pass_request = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 64; i = i + 1) begin
      pass_request = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, clock[7], clock[6], clock[5], clock[4], clock[3], clock[2], clock[1], clock[0], red, yellow, green);
      $fdisplay(r3e_stim, "%0d,%b", $time, pass_request);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
