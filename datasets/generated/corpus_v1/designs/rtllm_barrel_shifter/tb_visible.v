`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] in;
  reg [2:0] ctrl;
  wire [7:0] out;
  barrel_shifter dut(.in(in), .ctrl(ctrl), .out(out));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in,ctrl");
    in = 0;
    ctrl = 0;
    for (i = 0; i < 64; i = i + 1) begin
      in = $random(s);
      ctrl = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", i, out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, in, ctrl);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
