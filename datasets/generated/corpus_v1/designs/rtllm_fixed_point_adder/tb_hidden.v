`timescale 1ns/1ps
module r3e_tb;
  reg [31:0] a;
  reg [31:0] b;
  wire [31:0] c;
  fixed_point_adder dut(.a(a), .b(b), .c(c));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,c[31],c[30],c[29],c[28],c[27],c[26],c[25],c[24],c[23],c[22],c[21],c[20],c[19],c[18],c[17],c[16],c[15],c[14],c[13],c[12],c[11],c[10],c[9],c[8],c[7],c[6],c[5],c[4],c[3],c[2],c[1],c[0]");
    a = 0;
    b = 0;
    for (i = 0; i < 160; i = i + 1) begin
      a = $random(s);
      b = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", i, c[31], c[30], c[29], c[28], c[27], c[26], c[25], c[24], c[23], c[22], c[21], c[20], c[19], c[18], c[17], c[16], c[15], c[14], c[13], c[12], c[11], c[10], c[9], c[8], c[7], c[6], c[5], c[4], c[3], c[2], c[1], c[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
