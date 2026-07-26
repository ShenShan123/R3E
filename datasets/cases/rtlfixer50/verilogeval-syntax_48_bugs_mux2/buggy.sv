module top_module (
	input sel,
	input [7:0] a,
	input [7:0] b,
	output reg [7:0] out
);
always @(posedge clk or posedge sel)
	begin
		if (sel)
			out <= b;
		else
			out <= a;
	end

endmodule
