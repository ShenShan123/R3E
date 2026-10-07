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
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,w,x,y,z");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b,c");
    a = 0;
    b = 0;
    c = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      c = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, w, x, y, z);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, a, b, c);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
