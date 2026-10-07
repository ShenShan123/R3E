`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] a;
  reg [7:0] b;
  reg [7:0] c;
  reg [7:0] d;
  wire [7:0] min;
  TopModule dut(.a(a), .b(b), .c(c), .d(d), .min(min));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,min[7],min[6],min[5],min[4],min[3],min[2],min[1],min[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b,c,d");
    a = 0;
    b = 0;
    c = 0;
    d = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      c = $random(s);
      d = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", $time, min[7], min[6], min[5], min[4], min[3], min[2], min[1], min[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b", $time, a, b, c, d);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
