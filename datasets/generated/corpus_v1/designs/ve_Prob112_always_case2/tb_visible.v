`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] in;
  wire [1:0] pos;
  TopModule dut(.in(in), .pos(pos));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,pos[1],pos[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in");
    in = 0;
    for (i = 0; i < 64; i = i + 1) begin
      in = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", $time, pos[1], pos[0]);
      $fdisplay(r3e_stim, "%0d,%b", $time, in);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
