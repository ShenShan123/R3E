`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] code;
  wire [3:0] out;
  wire valid;
  TopModule dut(.code(code), .out(out), .valid(valid));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0],valid");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,code");
    code = 0;
    for (i = 0; i < 64; i = i + 1) begin
      code = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", i, out[3], out[2], out[1], out[0], valid);
      $fdisplay(r3e_stim, "%0d,%b", i, code);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
