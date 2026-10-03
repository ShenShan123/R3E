`timescale 1ns/1ps
module r3e_tb;
  reg [2:0] a;
  wire [15:0] q;
  TopModule dut(.a(a), .q(q));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,q[15],q[14],q[13],q[12],q[11],q[10],q[9],q[8],q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a");
    a = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, q[15], q[14], q[13], q[12], q[11], q[10], q[9], q[8], q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
      $fdisplay(r3e_stim, "%0d,%b", i, a);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
