module TopModule(
   input   clk,
   input   rst_n,

   output  reg [3:0] gray_out
);
reg  [3:0] bin_out;
wire [3:0] gray_wire;

always @(posedge clk or negedge rst_n)begin
   if(rst_n == 1'b0) begin
      bin_out <= 4'b0;
   end
   else begin
      bin_out[3] = gray_wire[3];
      bin_out[2] = gray_wire[2]^bin_out[3];
      bin_out[1] = gray_wire[1]^bin_out[2];
      bin_out[0] = gray_wire[0]^bin_out[1];
   end 
end
reg [3:0] bin_add_wire;
always @(posedge clk or negedge rst_n)begin
   if(rst_n == 1'b0) begin
      bin_add_wire <= 4'b0;
   end
   else begin
      bin_add_wire <= bin_out + 1'b1;
   end
end
assign gray_wire = (bin_add_wire >> 1) ^ bin_add_wire;

always @(*) begin
	if (rst_n == 1'b0)
		gray_out = 4'b0;
	else
		gray_out = gray_wire;
end
endmodule
