`timescale 1ns/1ps
module r3e_tb;
  reg clk = 0;
  reg rst_n;
  reg start;
  reg [15:0] ain;
  reg [15:0] bin;
  wire [31:0] yout;
  wire done;
  verified_multi_16bit dut(.clk(clk), .rst_n(rst_n), .start(start), .ain(ain), .bin(bin), .yout(yout), .done(done));
  integer f, i, s;
  always #5 clk = ~clk;
  initial begin
    s = 97;
    f = $fopen("trace_hidden.txt");
    $fdisplay(f, "time,yout[31],yout[30],yout[29],yout[28],yout[27],yout[26],yout[25],yout[24],yout[23],yout[22],yout[21],yout[20],yout[19],yout[18],yout[17],yout[16],yout[15],yout[14],yout[13],yout[12],yout[11],yout[10],yout[9],yout[8],yout[7],yout[6],yout[5],yout[4],yout[3],yout[2],yout[1],yout[0],done");
    start = 0;
    ain = 0;
    bin = 0;
    rst_n = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < 160; i = i + 1) begin
      start = $random(s);
      ain = $random(s);
      bin = $random(s);
      @(negedge clk);
      $fdisplay(f, "%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, yout[31], yout[30], yout[29], yout[28], yout[27], yout[26], yout[25], yout[24], yout[23], yout[22], yout[21], yout[20], yout[19], yout[18], yout[17], yout[16], yout[15], yout[14], yout[13], yout[12], yout[11], yout[10], yout[9], yout[8], yout[7], yout[6], yout[5], yout[4], yout[3], yout[2], yout[1], yout[0], done);
    end
    $fclose(f);
    $finish;
  end
endmodule
