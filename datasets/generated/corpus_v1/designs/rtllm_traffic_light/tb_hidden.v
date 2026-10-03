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
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,clock[7],clock[6],clock[5],clock[4],clock[3],clock[2],clock[1],clock[0],red,yellow,green");
    pass_request = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      pass_request = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, clock[7], clock[6], clock[5], clock[4], clock[3], clock[2], clock[1], clock[0], red, yellow, green);
    end
    $fclose(f);
    $finish;
  end
endmodule
