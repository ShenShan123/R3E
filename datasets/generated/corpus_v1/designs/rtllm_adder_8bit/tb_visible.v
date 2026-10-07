`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] a;
  reg [7:0] b;
  reg cin;
  wire [7:0] sum;
  wire cout;
  verified_adder_8bit dut(.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,sum[7],sum[6],sum[5],sum[4],sum[3],sum[2],sum[1],sum[0],cout");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b,cin");
    a = 0;
    b = 0;
    cin = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      cin = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, sum[7], sum[6], sum[5], sum[4], sum[3], sum[2], sum[1], sum[0], cout);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, a, b, cin);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
