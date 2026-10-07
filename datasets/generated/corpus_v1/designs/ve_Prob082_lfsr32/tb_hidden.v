`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg reset;
  wire [31:0] q;
  TopModule dut(.clk(clk), .reset(reset), .q(q));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,q[31],q[30],q[29],q[28],q[27],q[26],q[25],q[24],q[23],q[22],q[21],q[20],q[19],q[18],q[17],q[16],q[15],q[14],q[13],q[12],q[11],q[10],q[9],q[8],q[7],q[6],q[5],q[4],q[3],q[2],q[1],q[0]");
    reset = 1;
    repeat (2) @(negedge clk);
    reset = 0;
    for (i = 0; i < 160; i = i + 1) begin
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, q[31], q[30], q[29], q[28], q[27], q[26], q[25], q[24], q[23], q[22], q[21], q[20], q[19], q[18], q[17], q[16], q[15], q[14], q[13], q[12], q[11], q[10], q[9], q[8], q[7], q[6], q[5], q[4], q[3], q[2], q[1], q[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
