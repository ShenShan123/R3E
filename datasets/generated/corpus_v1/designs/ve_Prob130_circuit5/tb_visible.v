`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] a;
  reg [3:0] b;
  reg [3:0] c;
  reg [3:0] d;
  reg [3:0] e;
  wire [3:0] q;
  TopModule dut(.a(a), .b(b), .c(c), .d(d), .e(e), .q(q));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q[3],q[2],q[1],q[0]");
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
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, q[3], q[2], q[1], q[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b,%b", $time, a, b, c, d, e);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
