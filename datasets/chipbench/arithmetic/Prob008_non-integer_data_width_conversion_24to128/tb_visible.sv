`timescale 1 ps/1 ps
`define OK 12
`define INCORRECT 13


module stimulus_gen (
	input clk,
    // === Start your code here ===
	output logic rst_n,
	output logic valid_in,
	output logic [23:0] data_in
    // === End your code here ===
);
	// === Start your code here ===
	initial begin
		rst_n = 0;
		valid_in = 0;
		data_in = 0;
		repeat(10) @(posedge clk);
		rst_n = 1;
		@(posedge clk);
		
		// Test first group: 6 x 24-bit inputs to form 128-bit output
		// Inputs: 0x111111, 0x222222, 0x333333, 0x444444, 0x555555, 0x666666
		valid_in = 1;
		data_in = 24'h111111; @(posedge clk);
		data_in = 24'h222222; @(posedge clk);
		data_in = 24'h333333; @(posedge clk);
		data_in = 24'h444444; @(posedge clk);
		data_in = 24'h555555; @(posedge clk);
		data_in = 24'h666666; @(posedge clk);
		
		// Test second group: 6 x 24-bit inputs
		data_in = 24'h777777; @(posedge clk);
		data_in = 24'h888888; @(posedge clk);
		data_in = 24'h999999; @(posedge clk);
		data_in = 24'haaaaaa; @(posedge clk);
		data_in = 24'hbbbbbb; @(posedge clk);
		data_in = 24'hcccccc; @(posedge clk);
		
		// Test with valid_in low (should ignore data)
		valid_in = 0;
		data_in = 24'hdddddd; @(posedge clk);
		data_in = 24'heeeeee; @(posedge clk);
		
		// Test third group: 6 x 24-bit inputs
		valid_in = 1;
		data_in = 24'hffffff; @(posedge clk);
		data_in = 24'h000000; @(posedge clk);
		data_in = 24'h123456; @(posedge clk);
		data_in = 24'h789abc; @(posedge clk);
		data_in = 24'hdef012; @(posedge clk);
		data_in = 24'h345678; @(posedge clk);
		
		// Random testing
		repeat(200) @(posedge clk, negedge clk) begin
			valid_in <= $random;
			data_in <= $random % (1<<24);
		end
		
		#1 $finish;
	end
	// === End your code here ===
	
endmodule

module tb();

	// === Start your code here ===
	typedef struct packed {
		int errors;
		int errortime;
		int errors_valid_out;
		int errortime_valid_out;
		int errors_data_out;
		int errortime_data_out;

		int clocks;
	} stats;
	// === End your code here ===
	
	stats stats1;
	
	
	wire[511:0] wavedrom_title;
	wire wavedrom_enable;
	int wavedrom_hide_after_time;
	
	reg clk=0;
	initial forever
		#5 clk = ~clk;

	// === Start your code here ===
	logic rst_n;
	logic valid_in;
	logic [23:0] data_in;
	logic valid_out_ref;
	logic valid_out_dut;
	logic [127:0] data_out_ref;
	logic [127:0] data_out_dut;
	// === End your code here ===

	initial begin 
		// R3E: waveform dump disabled; use the visible CSV trace.
		// === Start your code here ===
		// R3E: waveform dump disabled; use the visible CSV trace.
		// === End your code here ===
	end


	wire tb_match;		// Verification
	wire tb_mismatch = ~tb_match;
	
	// === Start your code here ===
	stimulus_gen stim1 (
		.clk,
		.rst_n,
		.valid_in,
		.data_in );
		
	RefModule good1 (
		.clk,
		.rst_n,
		.valid_in,
		.data_in,
		.valid_out(valid_out_ref),
		.data_out(data_out_ref) );
		
	TopModule top_module1 (
		.clk,
		.rst_n,
		.valid_in,
		.data_in,
		.valid_out(valid_out_dut),
		.data_out(data_out_dut) );
	// === End your code here ===

	
	bit strobe = 0;
	task wait_for_end_of_timestep;
		repeat(5) begin
			strobe <= !strobe;  // Try to delay until the very end of the time step.
			@(strobe);
		end
	endtask	

	
	final begin
		// === Start your code here ===
		if (stats1.errors_valid_out) $display("Hint: Output '%s' has %0d mismatches. First mismatch occurred at time %0d.", "valid_out", stats1.errors_valid_out, stats1.errortime_valid_out);
		else $display("Hint: Output '%s' has no mismatches.", "valid_out");
		if (stats1.errors_data_out) $display("Hint: Output '%s' has %0d mismatches. First mismatch occurred at time %0d.", "data_out", stats1.errors_data_out, stats1.errortime_data_out);
		else $display("Hint: Output '%s' has no mismatches.", "data_out");
        // === End your code here ===

		$display("Hint: Total mismatched samples is %1d out of %1d samples\n", stats1.errors, stats1.clocks);
		$display("Simulation finished at %0d ps", $time);
		$display("Mismatches: %1d in %1d samples", stats1.errors, stats1.clocks);
	end
	
	// Verification: XORs on the right makes any X in good_vector match anything, but X in dut_vector will only match X.
	assign tb_match = ( { valid_out_ref, data_out_ref } === ( { valid_out_ref, data_out_ref } ^ { valid_out_dut, data_out_dut } ^ { valid_out_ref, data_out_ref } ) );
	// Use explicit sensitivity list here. @(*) causes NetProc::nex_input() to be called when trying to compute
	// the sensitivity list of the @(strobe) process, which isn't implemented.
	always @(posedge clk, negedge clk) begin

		stats1.clocks++;
		if (!tb_match) begin
			if (stats1.errors == 0) stats1.errortime = $time;
			stats1.errors++;
		end
		// === Start your code here ===
		if (valid_out_ref !== ( valid_out_ref ^ valid_out_dut ^ valid_out_ref ))
		begin if (stats1.errors_valid_out == 0) stats1.errortime_valid_out = $time;
			stats1.errors_valid_out = stats1.errors_valid_out+1'b1; end
		if (data_out_ref !== ( data_out_ref ^ data_out_dut ^ data_out_ref ))
		begin if (stats1.errors_data_out == 0) stats1.errortime_data_out = $time;
			stats1.errors_data_out = stats1.errors_data_out+1'b1; end
         // === End your code here ===

	end

   // add timeout after 100K cycles
   initial begin
     #1000000
     $display("TIMEOUT");
     $finish();
   end


    // R3E visible output trace; one binary digit per DUT output bit.
    // Preserve upstream stimuli/checker. Sample after NBA updates settle.
    integer r3e_trace_fd, r3e_stimulus_fd, r3e_stimulation_fd;
    initial begin
        r3e_trace_fd = $fopen("trace_visible.txt", "w");
        if (r3e_trace_fd == 0) $fatal(1, "Cannot open trace_visible.txt");
        $fdisplay(r3e_trace_fd, "time,valid_out,data_out[127],data_out[126],data_out[125],data_out[124],data_out[123],data_out[122],data_out[121],data_out[120],data_out[119],data_out[118],data_out[117],data_out[116],data_out[115],data_out[114],data_out[113],data_out[112],data_out[111],data_out[110],data_out[109],data_out[108],data_out[107],data_out[106],data_out[105],data_out[104],data_out[103],data_out[102],data_out[101],data_out[100],data_out[99],data_out[98],data_out[97],data_out[96],data_out[95],data_out[94],data_out[93],data_out[92],data_out[91],data_out[90],data_out[89],data_out[88],data_out[87],data_out[86],data_out[85],data_out[84],data_out[83],data_out[82],data_out[81],data_out[80],data_out[79],data_out[78],data_out[77],data_out[76],data_out[75],data_out[74],data_out[73],data_out[72],data_out[71],data_out[70],data_out[69],data_out[68],data_out[67],data_out[66],data_out[65],data_out[64],data_out[63],data_out[62],data_out[61],data_out[60],data_out[59],data_out[58],data_out[57],data_out[56],data_out[55],data_out[54],data_out[53],data_out[52],data_out[51],data_out[50],data_out[49],data_out[48],data_out[47],data_out[46],data_out[45],data_out[44],data_out[43],data_out[42],data_out[41],data_out[40],data_out[39],data_out[38],data_out[37],data_out[36],data_out[35],data_out[34],data_out[33],data_out[32],data_out[31],data_out[30],data_out[29],data_out[28],data_out[27],data_out[26],data_out[25],data_out[24],data_out[23],data_out[22],data_out[21],data_out[20],data_out[19],data_out[18],data_out[17],data_out[16],data_out[15],data_out[14],data_out[13],data_out[12],data_out[11],data_out[10],data_out[9],data_out[8],data_out[7],data_out[6],data_out[5],data_out[4],data_out[3],data_out[2],data_out[1],data_out[0]");
        r3e_stimulus_fd = $fopen("stimulus_visible.txt", "w");
        r3e_stimulation_fd = $fopen("stimulation_visible.txt", "w");
        if (r3e_stimulus_fd == 0 || r3e_stimulation_fd == 0)
            $fatal(1, "Cannot open visible input logs");
        $fdisplay(r3e_stimulus_fd, "# Inputs sampled after NBA on both tb.clk edges, row-aligned with trace_visible.txt; clocks and resets included");
        $fdisplay(r3e_stimulation_fd, "# Inputs sampled after NBA on both tb.clk edges, row-aligned with trace_visible.txt; clocks and resets included");
        $fdisplay(r3e_stimulus_fd, "time,clk,rst_n,valid_in,data_in");
        $fdisplay(r3e_stimulation_fd, "time,clk,rst_n,valid_in,data_in");
    end
    always @(posedge clk or negedge clk) begin
        $fstrobe(r3e_trace_fd, "%0t,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, valid_out_dut, data_out_dut[127], data_out_dut[126], data_out_dut[125], data_out_dut[124], data_out_dut[123], data_out_dut[122], data_out_dut[121], data_out_dut[120], data_out_dut[119], data_out_dut[118], data_out_dut[117], data_out_dut[116], data_out_dut[115], data_out_dut[114], data_out_dut[113], data_out_dut[112], data_out_dut[111], data_out_dut[110], data_out_dut[109], data_out_dut[108], data_out_dut[107], data_out_dut[106], data_out_dut[105], data_out_dut[104], data_out_dut[103], data_out_dut[102], data_out_dut[101], data_out_dut[100], data_out_dut[99], data_out_dut[98], data_out_dut[97], data_out_dut[96], data_out_dut[95], data_out_dut[94], data_out_dut[93], data_out_dut[92], data_out_dut[91], data_out_dut[90], data_out_dut[89], data_out_dut[88], data_out_dut[87], data_out_dut[86], data_out_dut[85], data_out_dut[84], data_out_dut[83], data_out_dut[82], data_out_dut[81], data_out_dut[80], data_out_dut[79], data_out_dut[78], data_out_dut[77], data_out_dut[76], data_out_dut[75], data_out_dut[74], data_out_dut[73], data_out_dut[72], data_out_dut[71], data_out_dut[70], data_out_dut[69], data_out_dut[68], data_out_dut[67], data_out_dut[66], data_out_dut[65], data_out_dut[64], data_out_dut[63], data_out_dut[62], data_out_dut[61], data_out_dut[60], data_out_dut[59], data_out_dut[58], data_out_dut[57], data_out_dut[56], data_out_dut[55], data_out_dut[54], data_out_dut[53], data_out_dut[52], data_out_dut[51], data_out_dut[50], data_out_dut[49], data_out_dut[48], data_out_dut[47], data_out_dut[46], data_out_dut[45], data_out_dut[44], data_out_dut[43], data_out_dut[42], data_out_dut[41], data_out_dut[40], data_out_dut[39], data_out_dut[38], data_out_dut[37], data_out_dut[36], data_out_dut[35], data_out_dut[34], data_out_dut[33], data_out_dut[32], data_out_dut[31], data_out_dut[30], data_out_dut[29], data_out_dut[28], data_out_dut[27], data_out_dut[26], data_out_dut[25], data_out_dut[24], data_out_dut[23], data_out_dut[22], data_out_dut[21], data_out_dut[20], data_out_dut[19], data_out_dut[18], data_out_dut[17], data_out_dut[16], data_out_dut[15], data_out_dut[14], data_out_dut[13], data_out_dut[12], data_out_dut[11], data_out_dut[10], data_out_dut[9], data_out_dut[8], data_out_dut[7], data_out_dut[6], data_out_dut[5], data_out_dut[4], data_out_dut[3], data_out_dut[2], data_out_dut[1], data_out_dut[0]);
        $fstrobe(r3e_stimulus_fd, "%0t,%b,%b,%b,%b", $time, clk, rst_n, valid_in, data_in);
        $fstrobe(r3e_stimulation_fd, "%0t,%b,%b,%b,%b", $time, clk, rst_n, valid_in, data_in);
    end
    // Simulator exit flushes/closes the file. Closing it in a final block
    // could discard a pending postponed-region sample on the last edge.

endmodule
