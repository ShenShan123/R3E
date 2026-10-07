`timescale 1ns/1ps
module r3e_tb;
  reg [255:0] in;
  reg [7:0] sel;
  wire out;
  TopModule dut(.in(in), .sel(sel), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out");
    in = 0;
    sel = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = {$random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s)};
      sel = $random(s);
      #5;
      $fdisplay(f, "%0d,%b", $time, out);
    end
    $fclose(f);
    $finish;
  end
endmodule
