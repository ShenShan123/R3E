`timescale 1ns/1ps
module r3e_tb;
  reg c;
  reg d;
  wire [3:0] mux_in;
  TopModule dut(.c(c), .d(d), .mux_in(mux_in));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,mux_in[3],mux_in[2],mux_in[1],mux_in[0]");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,c,d");
    c = 0;
    d = 0;
    for (i = 0; i < 64; i = i + 1) begin
      c = $random(s);
      d = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b", i, mux_in[3], mux_in[2], mux_in[1], mux_in[0]);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, c, d);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
