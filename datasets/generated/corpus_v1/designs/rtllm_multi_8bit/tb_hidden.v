`timescale 1ns/1ps
module r3e_tb;
  reg [7:0] A;
  reg [7:0] B;
  wire [15:0] product;
  multi_8bit dut(.A(A), .B(B), .product(product));
  integer f, i, s;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,product[15],product[14],product[13],product[12],product[11],product[10],product[9],product[8],product[7],product[6],product[5],product[4],product[3],product[2],product[1],product[0]");
    A = 0;
    B = 0;
    for (i = 0; i < 160; i = i + 1) begin
      A = $random(s);
      B = $random(s);
      #5;
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, product[15], product[14], product[13], product[12], product[11], product[10], product[9], product[8], product[7], product[6], product[5], product[4], product[3], product[2], product[1], product[0]);
    end
    $fclose(f);
    $finish;
  end
endmodule
