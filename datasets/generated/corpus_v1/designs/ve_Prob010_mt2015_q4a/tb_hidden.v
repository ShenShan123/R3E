`timescale 1ns/1ps
module r3e_tb;
  reg x;
  reg y;
  wire z;
  TopModule dut(.x(x), .y(y), .z(z));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,z");
    x = 0;
    y = 0;
    for (i = 0; i < 160; i = i + 1) begin
      x = $random(s);
      y = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", i, z);
    end
    $fclose(f);
    $finish;
  end
endmodule
