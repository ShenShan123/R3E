`timescale 1ns/1ps
module r3e_tb;
  reg sel;
  reg [7:0] a;
  reg [7:0] b;
  wire [7:0] out;
  TopModule dut(.sel(sel), .a(a), .b(b), .out(out));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,sel,a,b");
    sel = 0;
    a = 0;
    b = 0;
    for (i = 0; i < 64; i = i + 1) begin
      sel = $random(s);
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", $time, out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, sel, a, b);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
