module top_module (
	input sel,
	input [7:0] a,
	input [7:0] b,
	output reg [7:0] out
);
always @(posedge clk or posedge reset) begin
	if (reset) begin
		out <= 8'b0;
	end else begin
		if (sel == 0) begin
			out <= b;
		end else begin
			out <= a;
		end
	end
end

endmodule
