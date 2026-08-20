module formal_top;
  (* anyconst *) reg [3:0] a;
  (* anyconst *) reg [3:0] b;
  wire y;
  m dut(.a(a), .b(b), .y(y));
  always @* assert(y == ((a < 4) && (b > 2)));
endmodule
