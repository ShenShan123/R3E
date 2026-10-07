`timescale 1ns/1ps
module r3e_tb;
  reg [15:0] a;
  reg [15:0] b;
  reg Cin;
  wire [15:0] y;
  wire Co;
  verified_adder_16bit dut(.a(a), .b(b), .Cin(Cin), .y(y), .Co(Co));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,y[15],y[14],y[13],y[12],y[11],y[10],y[9],y[8],y[7],y[6],y[5],y[4],y[3],y[2],y[1],y[0],Co");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,a,b,Cin");
    a = 0;
    b = 0;
    Cin = 0;
    for (i = 0; i < 64; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      Cin = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, y[15], y[14], y[13], y[12], y[11], y[10], y[9], y[8], y[7], y[6], y[5], y[4], y[3], y[2], y[1], y[0], Co);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", $time, a, b, Cin);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
