`timescale 1ns/1ps
module r3e_tb;
  reg CLK = 0;
  reg RST;
  wire [5:0] Hours;
  wire [5:0] Mins;
  wire [5:0] Secs;
  verified_calendar dut(.CLK(CLK), .RST(RST), .Hours(Hours), .Mins(Mins), .Secs(Secs));
  integer f, i, s, r3e_stim;
  always #5 CLK = ~CLK;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,Hours[5],Hours[4],Hours[3],Hours[2],Hours[1],Hours[0],Mins[5],Mins[4],Mins[3],Mins[2],Mins[1],Mins[0],Secs[5],Secs[4],Secs[3],Secs[2],Secs[1],Secs[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset RST held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,");
    RST = 1;
    repeat (2) @(negedge CLK);
    RST = 0;
    for (i = 0; i < 64; i = i + 1) begin
      @(negedge CLK);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, Hours[5], Hours[4], Hours[3], Hours[2], Hours[1], Hours[0], Mins[5], Mins[4], Mins[3], Mins[2], Mins[1], Mins[0], Secs[5], Secs[4], Secs[3], Secs[2], Secs[1], Secs[0]);
      $fdisplay(r3e_stim, "%0d", i);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
