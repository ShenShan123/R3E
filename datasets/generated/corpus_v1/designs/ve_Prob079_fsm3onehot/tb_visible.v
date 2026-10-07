`timescale 1ns/1ps
module r3e_tb;
  reg in;
  reg [3:0] state;
  wire [3:0] next_state;
  wire out;
  TopModule dut(.in(in), .state(state), .next_state(next_state), .out(out));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,next_state[3],next_state[2],next_state[1],next_state[0],out");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,in,state");
    in = 0;
    state = 0;
    for (i = 0; i < 64; i = i + 1) begin
      in = $random(s);
      state = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b", $time, next_state[3], next_state[2], next_state[1], next_state[0], out);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, in, state);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
