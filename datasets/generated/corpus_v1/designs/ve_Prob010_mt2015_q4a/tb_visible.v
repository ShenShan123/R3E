`timescale 1ns/1ps
module r3e_tb;
  reg x;
  reg y;
  wire z;
  TopModule dut(.x(x), .y(y), .z(z));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,z");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,x,y");
    x = 0;
    y = 0;
    for (i = 0; i < 64; i = i + 1) begin
      x = $random(s);
      y = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", i, z);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, x, y);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
