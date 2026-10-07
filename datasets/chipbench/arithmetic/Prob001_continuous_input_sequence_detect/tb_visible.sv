`timescale 1 ps/1 ps
`define OK 12
`define INCORRECT 13
`default_nettype none

module stimulus_gen (
    input wire clk,
    output reg rst_n,
    output reg a,
    input wire tb_match,
    input wire match_dut
);
    bit failed = 0;

    always @(posedge clk, negedge clk)
        if (!tb_match)
            failed <= 1;

    initial begin
        @(posedge clk);
        failed <= 0;
        rst_n <= 0;
        a <= 0;

        // Initial reset
        @(posedge clk);
        rst_n <= 1;

        // Test Case 1: No match
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 1;
        @(posedge clk) a <= 1;
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 1;
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 0;

        // Test Case 2: Matching sequence (0111_0001)
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 1;
        @(posedge clk) a <= 1;
        @(posedge clk) a <= 1;
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 0;
        @(posedge clk) a <= 1;

        // Test Case 3: Randomized input
        repeat (100) @(posedge clk) begin
            a <= $random;
        end

        // Test Case 4: Edge cases with reset
        @(posedge clk);
        rst_n <= 0;
        @(posedge clk);
        rst_n <= 1;
        repeat (8) @(posedge clk) a <= 1;

        // Add delay to observe outputs
        repeat (100) @(posedge clk);

        #1 $finish;
    end
endmodule

module tb();

    typedef struct packed {
        int errors;
        int errortime;
        int errors_match;
        int errortime_match;

        int clocks;
    } stats;

    stats stats1;

    wire [511:0] wavedrom_title;
    wire wavedrom_enable;
    int wavedrom_hide_after_time;

    reg clk = 0;
    initial forever #5 clk = ~clk;

    logic rst_n;
    logic a;
    logic match_ref;
    logic match_dut;

    initial begin
        // R3E: waveform dump disabled; use the visible CSV trace.
        // R3E: waveform dump disabled; use the visible CSV trace.
    end

    wire tb_match;  // Verification
    wire tb_mismatch = ~tb_match;

    stimulus_gen stim1 (
        .clk(clk),
        .rst_n(rst_n),
        .a(a),
        .tb_match(tb_match),
        .match_dut(match_dut)
    );

    RefModule good1 (
        .clk(clk),
        .rst_n(rst_n),
        .a(a),
        .match(match_ref)
    );

    TopModule top_module1 (
        .clk(clk),
        .rst_n(rst_n),
        .a(a),
        .match(match_dut)
    );

    bit strobe = 0;
    task wait_for_end_of_timestep;
        repeat (5) begin
            strobe <= !strobe;  // Try to delay until the very end of the time step.
            @(strobe);
        end
    endtask

    final begin
        if (stats1.errors_match) $display("Hint: Output '%s' has %0d mismatches. First mismatch occurred at time %0d.", "match", stats1.errors_match, stats1.errortime_match);
        else $display("Hint: Output '%s' has no mismatches.", "match");

        $display("Hint: Total mismatched samples is %1d out of %1d samples\n", stats1.errors, stats1.clocks);
        $display("Simulation finished at %0d ps", $time);
        $display("Mismatches: %1d in %1d samples", stats1.errors, stats1.clocks);
    end

    // Verification: XORs on the right makes any X in good_vector match anything, but X in dut_vector will only match X.
    assign tb_match = (
        {match_ref} ===
        ({match_ref} ^ {match_dut} ^ {match_ref})
    );

    always @(posedge clk, negedge clk) begin
        stats1.clocks++;
        if (!tb_match) begin
            if (stats1.errors == 0) stats1.errortime = $time;
            stats1.errors++;
        end
        if (match_ref !== (match_ref ^ match_dut ^ match_ref)) begin
            if (stats1.errors_match == 0) stats1.errortime_match = $time;
            stats1.errors_match++;
        end
    end

    // Add timeout after 100K cycles
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
        $fdisplay(r3e_trace_fd, "time,match");
        r3e_stimulus_fd = $fopen("stimulus_visible.txt", "w");
        r3e_stimulation_fd = $fopen("stimulation_visible.txt", "w");
        if (r3e_stimulus_fd == 0 || r3e_stimulation_fd == 0)
            $fatal(1, "Cannot open visible input logs");
        $fdisplay(r3e_stimulus_fd, "# Inputs sampled after NBA on both tb.clk edges, row-aligned with trace_visible.txt; clocks and resets included");
        $fdisplay(r3e_stimulation_fd, "# Inputs sampled after NBA on both tb.clk edges, row-aligned with trace_visible.txt; clocks and resets included");
        $fdisplay(r3e_stimulus_fd, "time,clk,rst_n,a");
        $fdisplay(r3e_stimulation_fd, "time,clk,rst_n,a");
    end
    always @(posedge clk or negedge clk) begin
        $fstrobe(r3e_trace_fd, "%0t,%b", $time, match_dut);
        $fstrobe(r3e_stimulus_fd, "%0t,%b,%b,%b", $time, clk, rst_n, a);
        $fstrobe(r3e_stimulation_fd, "%0t,%b,%b,%b", $time, clk, rst_n, a);
    end
    // Simulator exit flushes/closes the file. Closing it in a final block
    // could discard a pending postponed-region sample on the last edge.

endmodule