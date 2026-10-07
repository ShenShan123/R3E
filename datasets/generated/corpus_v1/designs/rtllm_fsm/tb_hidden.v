`timescale 1ns/1ps
module r3e_tb;
  reg CLK = 0;
  reg RST;
  reg IN;
  wire MATCH;
  verified_fsm dut(.IN(IN), .MATCH(MATCH), .CLK(CLK), .RST(RST));
  integer f, i, s;
  always #5 CLK = ~CLK;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,MATCH");
    IN = 0;
    RST = 1;
    repeat (2) @(negedge CLK);
    RST = 0;
    for (i = 0; i < 160; i = i + 1) begin
      IN = $random(s);
      @(negedge CLK);
      $fdisplay(f, "%0d,%b", $time, MATCH);
    end
    $fclose(f);
    $finish;
  end
endmodule
