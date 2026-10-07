`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] a;
  reg [7:0] b;
  reg cin;
  wire [7:0] sum;
  wire cout;
  verified_adder_8bit dut(.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,sum[7],sum[6],sum[5],sum[4],sum[3],sum[2],sum[1],sum[0],cout");
    a = 0;
    b = 0;
    cin = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      cin = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, sum[7], sum[6], sum[5], sum[4], sum[3], sum[2], sum[1], sum[0], cout);
    end
    $fclose(f);
    $finish;
  end
endmodule
