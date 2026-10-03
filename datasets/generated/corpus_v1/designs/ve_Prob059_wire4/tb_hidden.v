`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  reg c;
  wire w;
  wire x;
  wire y;
  wire z;
  TopModule dut(.a(a), .b(b), .c(c), .w(w), .x(x), .y(y), .z(z));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,w,x,y,z");
    a = 0;
    b = 0;
    c = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      c = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b", i, w, x, y, z);
    end
    $fclose(f);
    $finish;
  end
endmodule
