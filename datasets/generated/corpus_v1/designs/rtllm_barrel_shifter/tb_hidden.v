`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] in;
  reg [2:0] ctrl;
  wire [7:0] out;
  barrel_shifter dut(.in(in), .ctrl(ctrl), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0]");
    in = 0;
    ctrl = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      ctrl = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", i, out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
