`timescale 1 ps/1 ps
`define OK 12
`define INCORRECT 13


module stimulus_gen (
	input clk,
    // === Start your code here ===
	output logic [7:0] A
    // === End your code here ===
);
	// === Start your code here ===
    initial begin
		A <= 0;
		
		// Test basic cases
		@(posedge clk);
		A <= 8'd0;
		@(posedge clk);
		
		
		A <= 8'd200;
		@(posedge clk);
		
		A <= 8'd250;
		@(posedge clk);
		
		A <= 8'd255;
		@(posedge clk);
		
		// Test edge cases
		A <= 8'd128;
		@(posedge clk);
		
		A <= 8'd64;
		@(posedge clk);
		
		A <= 8'd32;
		@(posedge clk);
		
		A <= 8'd16;
		@(posedge clk);
		
		// Random test sequence
		repeat(200) @(posedge clk) begin
			A <= $random & 255;
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
		int errors_B;
		int errortime_B;

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
	logic [7:0] A;
	logic [15:0] B_ref;
	logic [15:0] B_dut;
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
		.A
	);
		
	RefModule good1 (
		.A,
		.B(B_ref)
	);
		
	TopModule top_module1 (
		.A,
		.B(B_dut)
	);
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
		if (stats1.errors_B) $display("Hint: Output '%s' has %0d mismatches. First mismatch occurred at time %0d.", "B", stats1.errors_B, stats1.errortime_B);
		else $display("Hint: Output '%s' has no mismatches.", "B");
        // === End your code here ===

		$display("Hint: Total mismatched samples is %1d out of %1d samples\n", stats1.errors, stats1.clocks);
		$display("Simulation finished at %0d ps", $time);
		$display("Mismatches: %1d in %1d samples", stats1.errors, stats1.clocks);
	end
	
	// Verification: XORs on the right makes any X in good_vector match anything, but X in dut_vector will only match X.
	assign tb_match = ( { B_ref } === ( { B_ref } ^ { B_dut } ^ { B_ref } ) );
	// Use explicit sensitivity list here. @(*) causes NetProc::nex_input() to be called when trying to compute
	// the sensitivity list of the @(strobe) process, which isn't implemented.
	always @(posedge clk, negedge clk) begin

		stats1.clocks++;
		if (!tb_match) begin
			if (stats1.errors == 0) stats1.errortime = $time;
			stats1.errors++;
		end
		// === Start your code here ===
		if (B_ref !== ( B_ref ^ B_dut ^ B_ref ))
		begin if (stats1.errors_B == 0) stats1.errortime_B = $time;
			stats1.errors_B = stats1.errors_B+1'b1; end
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
        $fdisplay(r3e_trace_fd, "time,B[15],B[14],B[13],B[12],B[11],B[10],B[9],B[8],B[7],B[6],B[5],B[4],B[3],B[2],B[1],B[0]");
        r3e_stimulus_fd = $fopen("stimulus_visible.txt", "w");
        r3e_stimulation_fd = $fopen("stimulation_visible.txt", "w");
        if (r3e_stimulus_fd == 0 || r3e_stimulation_fd == 0)
            $fatal(1, "Cannot open visible input logs");
        $fdisplay(r3e_stimulus_fd, "# Inputs sampled after NBA on both tb.clk edges, row-aligned with trace_visible.txt; clocks and resets included");
        $fdisplay(r3e_stimulation_fd, "# Inputs sampled after NBA on both tb.clk edges, row-aligned with trace_visible.txt; clocks and resets included");
        $fdisplay(r3e_stimulus_fd, "time,A");
        $fdisplay(r3e_stimulation_fd, "time,A");
    end
    always @(posedge clk or negedge clk) begin
        $fstrobe(r3e_trace_fd, "%0t,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b", $time, B_dut[15], B_dut[14], B_dut[13], B_dut[12], B_dut[11], B_dut[10], B_dut[9], B_dut[8], B_dut[7], B_dut[6], B_dut[5], B_dut[4], B_dut[3], B_dut[2], B_dut[1], B_dut[0]);
        $fstrobe(r3e_stimulus_fd, "%0t,%b", $time, A);
        $fstrobe(r3e_stimulation_fd, "%0t,%b", $time, A);
    end
    // Simulator exit flushes/closes the file. Closing it in a final block
    // could discard a pending postponed-region sample on the last edge.

endmodule
