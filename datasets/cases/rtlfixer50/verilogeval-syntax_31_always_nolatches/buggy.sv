module top_module (
	input [15:0] scancode,
	output reg left,
	output reg down,
	output reg right,
	output reg up
);
always @(posedge clk) begin
	if (scancode == 16'h006b)
		left <= 1;
	else
		left <= 0;

	if (scancode == 16'h0072)
		down <= 1;
	else
		down <= 0;

	if (scancode == 16'h0074)
		right <= 1;
	else
		right <= 0;

	if (scancode == 16'h0075)
		up <= 1;
	else
		up <= 0;
end

endmodule
