`timescale 1ns/1ps
module r3e_tb;
  reg in;
  reg [1:0] state;
  wire [1:0] next_state;
  wire out;
  TopModule dut(.in(in), .state(state), .next_state(next_state), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,next_state[1],next_state[0],out");
    in = 0;
    state = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      state = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", i, next_state[1], next_state[0], out);
    end
    $fclose(f);
    $finish;
  end
endmodule
