`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  reg c;
  reg d;
  reg e;
  wire [24:0] out;
  TopModule dut(.a(a), .b(b), .c(c), .d(d), .e(e), .out(out));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[24],out[23],out[22],out[21],out[20],out[19],out[18],out[17],out[16],out[15],out[14],out[13],out[12],out[11],out[10],out[9],out[8],out[7],out[6],out[5],out[4],out[3],out[2],out[1],out[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b,c,d,e");
    a = 0;
    b = 0;
    c = 0;
    d = 0;
    e = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      c = $random(s);
      d = $random(s);
      e = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, out[24], out[23], out[22], out[21], out[20], out[19], out[18], out[17], out[16], out[15], out[14], out[13], out[12], out[11], out[10], out[9], out[8], out[7], out[6], out[5], out[4], out[3], out[2], out[1], out[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b,%b", $time, a, b, c, d, e);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
