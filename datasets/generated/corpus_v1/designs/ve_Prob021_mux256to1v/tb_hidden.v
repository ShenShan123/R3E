`timescale 1ns/1ps
module r3e_tb;
  reg [1023:0] in;
  reg [7:0] sel;
  wire [3:0] out;
  TopModule dut(.in(in), .sel(sel), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0]");
    in = 0;
    sel = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = {$random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s), $random(s)};
      sel = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b", $time, out[3], out[2], out[1], out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
