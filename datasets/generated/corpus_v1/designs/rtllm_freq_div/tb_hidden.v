`timescale 1ns/1ps
module r3e_tb;
  reg CLK_in = 0;
  reg RST;
  wire CLK_50;
  wire CLK_10;
  wire CLK_1;
  freq_div dut(.CLK_in(CLK_in), .CLK_50(CLK_50), .CLK_10(CLK_10), .CLK_1(CLK_1), .RST(RST));
  integer f, i, s;
  always #5 CLK_in = ~CLK_in;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,CLK_50,CLK_10,CLK_1");
    RST = 1;
    repeat (2) @(negedge CLK_in);
    RST = 0;
    for (i = 0; i < 160; i = i + 1) begin
      @(negedge CLK_in);
      $fdisplay(f, "%0d,%b,%b,%b", i, CLK_50, CLK_10, CLK_1);
    end
    $fclose(f);
    $finish;
  end
endmodule
