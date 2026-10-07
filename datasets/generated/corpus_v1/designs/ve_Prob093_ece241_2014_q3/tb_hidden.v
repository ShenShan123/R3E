`timescale 1ns/1ps
module r3e_tb;
  reg c;
  reg d;
  wire [3:0] mux_in;
  TopModule dut(.c(c), .d(d), .mux_in(mux_in));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,mux_in[3],mux_in[2],mux_in[1],mux_in[0]");
    c = 0;
    d = 0;
    for (i = 0; i < 160; i = i + 1) begin
      c = $random(s);
      d = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, mux_in[3], mux_in[2], mux_in[1], mux_in[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
