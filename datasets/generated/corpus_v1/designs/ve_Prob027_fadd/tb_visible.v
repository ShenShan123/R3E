`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  reg cin;
  wire cout;
  wire sum;
  TopModule dut(.a(a), .b(b), .cin(cin), .cout(cout), .sum(sum));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,cout,sum");
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
      $fdisplay(f, "%0d,%b,%b", i, cout, sum);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b", i, a, b, cin);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
