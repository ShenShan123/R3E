`timescale 1ns/1ps
module r3e_tb;
  reg p1a;
  reg p1b;
  reg p1c;
  reg p1d;
  reg p2a;
  reg p2b;
  reg p2c;
  reg p2d;
  wire p1y;
  wire p2y;
  TopModule dut(.p1a(p1a), .p1b(p1b), .p1c(p1c), .p1d(p1d), .p1y(p1y), .p2a(p2a), .p2b(p2b), .p2c(p2c), .p2d(p2d), .p2y(p2y));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,p1y,p2y");
    p1a = 0;
    p1b = 0;
    p1c = 0;
    p1d = 0;
    p2a = 0;
    p2b = 0;
    p2c = 0;
    p2d = 0;
    for (i = 0; i < 160; i = i + 1) begin
      p1a = $random(s);
      p1b = $random(s);
      p1c = $random(s);
      p1d = $random(s);
      p2a = $random(s);
      p2b = $random(s);
      p2c = $random(s);
      p2d = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, p1y, p2y);
    end
    $fclose(f);
    $finish;
  end
endmodule
