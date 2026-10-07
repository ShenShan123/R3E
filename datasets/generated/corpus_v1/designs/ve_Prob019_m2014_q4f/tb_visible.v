`timescale 1ns/1ps
module r3e_tb;
  reg in1;
  reg in2;
  wire out;
  TopModule dut(.in1(in1), .in2(in2), .out(out));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in1,in2");
    in1 = 0;
    in2 = 0;
    for (i = 0; i < 64; i = i + 1) begin
      in1 = $random(s);
      in2 = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", $time, out);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, in1, in2);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
