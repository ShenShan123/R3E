module top_module (
	input [7:0] in,
	output reg [2:0] pos
);
always @(posedge clk) begin
		pos <= 3'b0;
		for (integer i = 0; i < 8; i = i + 1) begin
			if (in[i] == 1'b1) begin
				pos <= i[2:0];
				break;
			end
		end
	end

endmodule
