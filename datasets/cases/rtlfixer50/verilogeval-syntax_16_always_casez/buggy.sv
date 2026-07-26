module top_module (
	input [7:0] in,
	output reg [2:0] pos
);
always @(posedge clk) begin
	casez (in)
		8'bzzzzzzz1: pos = 3'h0;
		8'bz1zzzzzz: pos = 3'h6;
		8'bzz1zzzzz: pos = 3'h5;
		8'bzzz1zzzz: pos = 3'h4;
		8'bzzzz1zzz: pos = 3'h3;
		8'bzzzzz1zz: pos = 3'h2;
		8'bzzzzzz1z: pos = 3'h1;
		default: pos = 3'h0;
	endcase
end
endmodule
