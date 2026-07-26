module top_module(
	input a, 
	input b,
	output out
);
and_gate u1 (
	.a(a),
	.b(b),
	.out(out)
);

endmodule
