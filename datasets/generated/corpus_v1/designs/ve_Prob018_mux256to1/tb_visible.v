`timescale 1ns/1ps
module r3e_tb;
  reg [255:0] in;
  reg [7:0] sel;
  wire out;
  TopModule dut(.in(in), .sel(sel), .out(out));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in,sel");
    in = 0;
    sel = 0;
    for (i = 0; i < 64; i = i + 1) begin
      in = {$random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s)};
      sel = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", i, out);
      $fdisplay(r3e_stim, "%0d,%b,%b", i, in, sel);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
