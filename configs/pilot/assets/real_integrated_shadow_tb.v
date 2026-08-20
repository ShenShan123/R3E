module tb;
  reg [3:0] a;
  reg [3:0] b;
  wire y;
  integer i;
  integer j;
  integer mismatches;
  m dut(.a(a), .b(b), .y(y));
  initial begin
    mismatches = 0;
    for (i = 0; i < 8; i = i + 1) begin
      for (j = 0; j < 8; j = j + 1) begin
        a = i; b = j; #1;
        if (y !== ((i < 4) && (j > 2))) mismatches = mismatches + 1;
      end
    end
    if (mismatches == 0) begin
      $display("R3E_ORACLE pass=1 signature=none first=none topology=none");
      $display("R3E_WAVEFORM signal=none first_cycle=none cycle_offset=none relation=none assignment=none cone_depth=none pattern=none");
    end else begin
      $display("R3E_ORACLE pass=0 signature=comparator_boundary first=cycle4 topology=y");
      $display("R3E_WAVEFORM signal=y first_cycle=4 cycle_offset=0 relation=same_cycle assignment=continuous cone_depth=1 pattern=boundary_value_mismatch");
    end
    $finish;
  end
endmodule
