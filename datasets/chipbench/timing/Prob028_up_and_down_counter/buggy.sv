module TopModule(
	input clk,
	input rst_n,
	input mode,
	output reg [3:0]number,
	output reg zero
	);

	always @(*) begin
    if (!rst_n)
      zero = 1'b0;
    else if (number == 4'd0)
      zero = 1'b1;
    else
      zero = 1'b0;
  end
		
	always @(posedge clk or negedge rst_n)
		if (!rst_n)
			begin 
				number <= 4'b0;
			end
		else if(mode)
			begin
				if(number == 9)
					number <= 0;
				else
					number <= number + 1'd1;
			end
		else if(!mode)
			begin
				if(number == 0)
					number <= 9;
				else
					number <= number - 1'd1;
			end
		else number <= number;
endmodule
