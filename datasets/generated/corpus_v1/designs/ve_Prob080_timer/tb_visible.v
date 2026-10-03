`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg load;
  reg [9:0] data;
  wire tc;
  TopModule dut(.clk(clk), .load(load), .data(data), .tc(tc));
  integer f, i, s, r3e_stim;
  always #5 clk = ~clk;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,tc");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,load,data");
    load = 0;
    data = 0;
    repeat (2) @(negedge clk);
    for (i = 0; i < 64; i = i + 1) begin
      load = $random(s);
      data = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b", i, tc);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, load, data);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
