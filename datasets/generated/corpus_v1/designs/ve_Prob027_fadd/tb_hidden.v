`timescale 1ns/1ps
module r3e_tb;
  reg a;
  reg b;
  reg cin;
  wire cout;
  wire sum;
  TopModule dut(.a(a), .b(b), .cin(cin), .cout(cout), .sum(sum));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,cout,sum");
    a = 0;
    b = 0;
    cin = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      cin = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, cout, sum);
    end
    $fclose(f);
    $finish;
  end
endmodule
