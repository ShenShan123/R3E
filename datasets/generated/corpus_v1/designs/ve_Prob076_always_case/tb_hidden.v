`timescale 1ns/1ps
module r3e_tb;
  reg [2:0] sel;
  reg [3:0] data0;
  reg [3:0] data1;
  reg [3:0] data2;
  reg [3:0] data3;
  reg [3:0] data4;
  reg [3:0] data5;
  wire [3:0] out;
  TopModule dut(.sel(sel), .data0(data0), .data1(data1), .data2(data2), .data3(data3), .data4(data4), .data5(data5), .out(out));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,out[3],out[2],out[1],out[0]");
    sel = 0;
    data0 = 0;
    data1 = 0;
    data2 = 0;
    data3 = 0;
    data4 = 0;
    data5 = 0;
    for (i = 0; i < 160; i = i + 1) begin
      sel = $random(s);
      data0 = $random(s);
      data1 = $random(s);
      data2 = $random(s);
      data3 = $random(s);
      data4 = $random(s);
      data5 = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b", i, out[3], out[2], out[1], out[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
