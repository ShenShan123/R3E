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
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,heater,aircon,fan");
    mode = 0;
    too_cold = 0;
    too_hot = 0;
    fan_on = 0;
    for (i = 0; i < 160; i = i + 1) begin
      mode = $random(s);
      too_cold = $random(s);
      too_hot = $random(s);
      fan_on = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b", $time, heater, aircon, fan);
    end
    $fclose(f);
    $finish;
  end
endmodule
