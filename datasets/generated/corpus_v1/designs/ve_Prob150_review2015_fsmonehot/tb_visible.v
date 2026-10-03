`timescale 1ns/1ps
module r3e_tb;
  reg d;
  reg done_counting;
  reg ack;
  reg [9:0] state;
  wire B3_next;
  wire S_next;
  wire S1_next;
  wire Count_next;
  wire Wait_next;
  wire done;
  wire counting;
  wire shift_ena;
  TopModule dut(.d(d), .done_counting(done_counting), .ack(ack), .state(state), .B3_next(B3_next), .S_next(S_next), .S1_next(S1_next), .Count_next(Count_next), .Wait_next(Wait_next), .done(done), .counting(counting), .shift_ena(shift_ena));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,B3_next,S_next,S1_next,Count_next,Wait_next,done,counting,shift_ena");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,d,done_counting,ack,state");
    d = 0;
    done_counting = 0;
    ack = 0;
    state = 0;
    for (i = 0; i < 64; i = i + 1) begin
      d = $random(s);
      done_counting = $random(s);
      ack = $random(s);
      state = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b", i, B3_next, S_next, S1_next, Count_next, Wait_next, done, counting, shift_ena);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b", i, d, done_counting, ack, state);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
