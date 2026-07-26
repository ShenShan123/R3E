module top_module (
	input [7:0] in,
	output reg [2:0] pos
);
always @(posedge clk or posedge reset) begin
	if (reset) begin
		pos <= 3'h0;
	end else begin
		casez (in)
			8'b1??????? : pos <= 3'h0;
			8'bz1?????? : pos <= 3'h6;
			8'bzz1????? : pos <= 3'h5;
			8'bzzz1???? : pos <= 3'h4;
			8'bzzzz1??? : pos <= 3'h3;
			8'bzzzzz1?? : pos <= 3'h2;
			8'bzzzzzz1? : pos <= 3'h1;
			default     : pos <= 3'h0;
		endcase
	end
end

endmodule
