`timescale 1ns/1ps
module r3e_tb;
  reg CLK_in = 0;
  reg RST;
  wire CLK_50;
  wire CLK_10;
  wire CLK_1;
  freq_div dut(.CLK_in(CLK_in), .CLK_50(CLK_50), .CLK_10(CLK_10), .CLK_1(CLK_1), .RST(RST));
  integer f, i, s, r3e_stim;
  always #5 CLK_in = ~CLK_in;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,CLK_50,CLK_10,CLK_1");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#reset RST held at 1 for 2 cycles before cycle 0");
    $fdisplay(r3e_stim, "time,");
    RST = 1;
    repeat (2) @(negedge CLK_in);
    RST = 0;
    for (i = 0; i < 64; i = i + 1) begin
      @(negedge CLK_in);
      $fdisplay(f, "%0d,%b,%b,%b", i, CLK_50, CLK_10, CLK_1);
      $fdisplay(r3e_stim, "%0d", i);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
