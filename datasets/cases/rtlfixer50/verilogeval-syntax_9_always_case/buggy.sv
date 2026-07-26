module top_module (
	input [2:0] sel,
	input [3:0] data0,
	input [3:0] data1,
	input [3:0] data2,
	input [3:0] data3,
	input [3:0] data4,
	input [3:0] data5,
	output reg [3:0] out
);
always @(posedge clk) begin
	if (sel == 0)
		out <= data0;
	else if (sel == 1)
		out <= data1;
	else if (sel == 2)
		out <= data2;
	else if (sel == 3)
		out <= data3;
	else if (sel == 4)
		out <= data4;
	else if (sel == 5)
		out <= data5;
	else
		out <= 0;
end
endmodule
