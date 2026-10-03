`timescale 1ns/1ps
module r3e_tb;
  reg CLK = 0;
  reg RST;
  reg IN;
  wire MATCH;
  verified_fsm dut(.IN(IN), .MATCH(MATCH), .CLK(CLK), .RST(RST));
  integer f, i, s, r3e_stim;
  always #5 CLK = ~CLK;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,MATCH");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset RST held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,IN");
    IN = 0;
    RST = 1;
    repeat (2) @(negedge CLK);
    RST = 0;
    for (i = 0; i < 64; i = i + 1) begin
      IN = $random(s);
      @(negedge CLK);
      $fdisplay(f, "%0d,%b", i, MATCH);
      $fdisplay(r3e_stim, "%0d,%b", i, IN);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
