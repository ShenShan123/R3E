`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] code;
  wire [3:0] out;
  wire valid;
  TopModule dut(.code(code), .out(out), .valid(valid));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0],valid");
    code = 0;
    for (i = 0; i < 160; i = i + 1) begin
      code = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", i, out[3], out[2], out[1], out[0], valid);
    end
    $fclose(f);
    $finish;
  end
endmodule
