module top_module(
	input a, 
	input b,
	output out
);
always @(posedge clk) begin
	out <= a & b;
end

endmodule
