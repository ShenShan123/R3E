module top_module (
	input a, 
	input b, 
	output q
);
always @(posedge clk) begin 
	q <= a & b; 
end 

endmodule
