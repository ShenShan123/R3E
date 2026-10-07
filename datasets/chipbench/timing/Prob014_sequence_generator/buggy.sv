module TopModule(
    input clk,
    input rst_n,
    output reg data
    );
     
    reg [5:0] q;
     
    always @(*) begin
        if (!rst_n)
            q = 6'b001011;
        else
            q = {q[4:0], q[5]};
    end

    always @(*) begin
        if (!rst_n)
            data = 1'b0;
        else
            data = q[5];
    end
endmodule
