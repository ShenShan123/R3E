`timescale 1ns/1ps
module r3e_tb;
  reg [2:0] vec;
  wire [2:0] outv;
  wire o2;
  wire o1;
  wire o0;
  TopModule dut(.vec(vec), .outv(outv), .o2(o2), .o1(o1), .o0(o0));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,outv[2],outv[1],outv[0],o2,o1,o0");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,vec");
    vec = 0;
    for (i = 0; i < 64; i = i + 1) begin
      vec = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b", i, outv[2], outv[1], outv[0], o2, o1, o0);
      $fdisplay(r3e_stim, "%0d,%b", i, vec);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
