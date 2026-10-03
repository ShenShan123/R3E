`timescale 1ns/1ps
module r3e_tb;
  reg p1a;
  reg p1b;
  reg p1c;
  reg p1d;
  reg p1e;
  reg p1f;
  reg p2a;
  reg p2b;
  reg p2c;
  reg p2d;
  wire p1y;
  wire p2y;
  TopModule dut(.p1a(p1a), .p1b(p1b), .p1c(p1c), .p1d(p1d), .p1e(p1e), .p1f(p1f), .p1y(p1y), .p2a(p2a), .p2b(p2b), .p2c(p2c), .p2d(p2d), .p2y(p2y));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,p1y,p2y");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,p1a,p1b,p1c,p1d,p1e,p1f,p2a,p2b,p2c,p2d");
    p1a = 0;
    p1b = 0;
    p1c = 0;
    p1d = 0;
    p1e = 0;
    p1f = 0;
    p2a = 0;
    p2b = 0;
    p2c = 0;
    p2d = 0;
    for (i = 0; i < 64; i = i + 1) begin
      p1a = $random(s);
      p1b = $random(s);
      p1c = $random(s);
      p1d = $random(s);
      p1e = $random(s);
      p1f = $random(s);
      p2a = $random(s);
      p2b = $random(s);
      p2c = $random(s);
      p2d = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, p1y, p2y);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, p1a, p1b, p1c, p1d, p1e, p1f, p2a, p2b, p2c, p2d);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
