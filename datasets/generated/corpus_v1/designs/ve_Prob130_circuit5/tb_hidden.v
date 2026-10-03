`timescale 1ns/1ps
module r3e_tb;
  reg [3:0] a;
  reg [3:0] b;
  reg [3:0] c;
  reg [3:0] d;
  reg [3:0] e;
  wire [3:0] q;
  TopModule dut(.a(a), .b(b), .c(c), .d(d), .e(e), .q(q));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[3],q[2],q[1],q[0]");
    a = 0;
    b = 0;
    c = 0;
    d = 0;
    e = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      c = $random(s);
      d = $random(s);
      e = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b", i, q[3], q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
