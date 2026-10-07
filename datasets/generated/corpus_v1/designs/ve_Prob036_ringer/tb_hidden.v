`timescale 1ns/1ps
module r3e_tb;
  reg ring;
  reg vibrate_mode;
  wire ringer;
  wire motor;
  TopModule dut(.ring(ring), .vibrate_mode(vibrate_mode), .ringer(ringer), .motor(motor));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,ringer,motor");
    ring = 0;
    vibrate_mode = 0;
    for (i = 0; i < 160; i = i + 1) begin
      ring = $random(s);
      vibrate_mode = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", $time, ringer, motor);
    end
    $fclose(f);
    $finish;
  end
endmodule
