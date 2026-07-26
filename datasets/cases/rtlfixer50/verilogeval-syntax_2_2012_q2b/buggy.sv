module top_module (
	input [5:0] y,
	input w,
	output Y1,
	output Y3
);
wire Y1;
wire Y3;

assign Y1 = y[4] | y[5];
assign Y3 = y[2] | y[3];


endmodule
