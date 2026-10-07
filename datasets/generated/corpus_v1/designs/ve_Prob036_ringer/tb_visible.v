`timescale 1ns/1ps
module r3e_tb;
  reg ring;
  reg vibrate_mode;
  wire ringer;
  wire motor;
  TopModule dut(.ring(ring), .vibrate_mode(vibrate_mode), .ringer(ringer), .motor(motor));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,ringer,motor");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,ring,vibrate_mode");
    ring = 0;
    vibrate_mode = 0;
    for (i = 0; i < 64; i = i + 1) begin
      ring = $random(s);
      vibrate_mode = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", $time, ringer, motor);
      $fdisplay(r3e_stim, "%0d,%b,%b", $time, ring, vibrate_mode);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
