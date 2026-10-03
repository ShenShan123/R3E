`timescale 1ns/1ps
module r3e_tb;
  reg cpu_overheated;
  reg arrived;
  reg gas_tank_empty;
  wire shut_off_computer;
  wire keep_driving;
  TopModule dut(.cpu_overheated(cpu_overheated), .shut_off_computer(shut_off_computer), .arrived(arrived), .gas_tank_empty(gas_tank_empty), .keep_driving(keep_driving));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,shut_off_computer,keep_driving");
    cpu_overheated = 0;
    arrived = 0;
    gas_tank_empty = 0;
    for (i = 0; i < 160; i = i + 1) begin
      cpu_overheated = $random(s);
      arrived = $random(s);
      gas_tank_empty = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b", i, shut_off_computer, keep_driving);
    end
    $fclose(f);
    $finish;
  end
endmodule
