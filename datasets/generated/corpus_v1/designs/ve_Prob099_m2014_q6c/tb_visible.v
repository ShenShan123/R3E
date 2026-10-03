`timescale 1ns/1ps
module r3e_tb;
  reg [5:0] y;
  reg w;
  wire Y1;
  wire Y3;
  TopModule dut(.y(y), .w(w), .Y1(Y1), .Y3(Y3));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,Y1,Y3");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,y,w");
    y = 0;
    w = 0;
    for (i = 0; i < 64; i = i + 1) begin
      y = $random(s);
      w = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, Y1, Y3);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, y, w);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
