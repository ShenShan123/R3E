`timescale 1ns/1ps
module r3e_tb;
  reg mode;
  reg too_cold;
  reg too_hot;
  reg fan_on;
  wire heater;
  wire aircon;
  wire fan;
  TopModule dut(.mode(mode), .too_cold(too_cold), .too_hot(too_hot), .fan_on(fan_on), .heater(heater), .aircon(aircon), .fan(fan));
  integer f, i, s, r3e_stim;
  initial begin
    s = 11;
    f = $fopen("trace_visible.txt");
    $fdisplay(f, "time,heater,aircon,fan");
    r3e_stim = $fopen("stimulus_visible.txt");
    $fdisplay(r3e_stim, "#no reset");
    $fdisplay(r3e_stim, "time,mode,too_cold,too_hot,fan_on");
    mode = 0;
    too_cold = 0;
    too_hot = 0;
    fan_on = 0;
    for (i = 0; i < 64; i = i + 1) begin
      mode = $random(s);
      too_cold = $random(s);
      too_hot = $random(s);
      fan_on = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", i, heater, aircon, fan);
      $fdisplay(r3e_stim, "%0d,%b,%b,%b,%b", i, mode, too_cold, too_hot, fan_on);
    end
    $fclose(f);
    $fclose(r3e_stim);
    $finish;
  end
endmodule
