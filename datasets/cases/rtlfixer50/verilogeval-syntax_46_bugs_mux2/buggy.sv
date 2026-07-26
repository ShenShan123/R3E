module top_module (
	input sel,
	input [7:0] a,
	input [7:0] b,
	output reg [7:0] out
);
mux4_1 mux4_1_inst (
		.sel (sel),
		.a (a),
		.b (b),
		.out (out)
	);

endmodule
