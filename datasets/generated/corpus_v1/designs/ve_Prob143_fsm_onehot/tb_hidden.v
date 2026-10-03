`timescale 1ns/1ps
module r3e_tb;
  reg in;
  reg [9:0] state;
  wire [9:0] next_state;
  wire out1;
  wire out2;
  TopModule dut(.in(in), .state(state), .next_state(next_state), .out1(out1), .out2(out2));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,next_state[9],next_state[8],next_state[7],next_state[6],next_state[5],next_state[4],next_state[3],next_state[2],next_state[1],next_state[0],out1,out2");
    in = 0;
    state = 0;
    for (i = 0; i < 160; i = i + 1) begin
      in = $random(s);
      state = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, next_state[9], next_state[8], next_state[7], next_state[6], next_state[5], next_state[4], next_state[3], next_state[2], next_state[1], next_state[0], out1, out2);
    end
    $fclose(f);
    $finish;
  end
endmodule
