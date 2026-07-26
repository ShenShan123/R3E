def classify_frontend_failure(
    iverilog_ok: bool,
    yosys_ok: bool,
    text: str,
    iverilog_returncode: int = 0,
    yosys_returncode: int = 0,
) -> str:
    t = text.lower()

    # Both tools passed: must be PASS, regardless of warning text.
    if iverilog_ok and yosys_ok:
        return "PASS"

    # Real timeout only when tool returncode or wrapper marker indicates timeout.
    if (
        iverilog_returncode == 124
        or yosys_returncode == 124
        or "[timeout]" in t
        or "command exceeded" in t
        or "timed out" in t
    ):
        return "F7_TOOL_TIMEOUT"

    if not iverilog_ok:
        if "include file" in t and "not found" in t:
            return "F0_INCLUDE_NOT_FOUND"
        if "syntax error" in t:
            return "F0_SYNTAX_PARSE_ERROR"
        if "not a valid l-value" in t and "declared here as wire" in t:
            return "F4_WIRE_ASSIGNED_IN_PROCEDURAL_BLOCK"
        if "unknown module" in t or "unable to bind" in t or "module not found" in t:
            return "F1_MODULE_CLOSURE_ERROR"
        if "parameter" in t or "localparam" in t:
            return "F2_PARAMETER_ELABORATION_ERROR"
        if "width" in t or "range" in t or "part select" in t:
            return "F3_WIDTH_OR_RANGE_ERROR"
        if "multiple drivers" in t or "unresolved wire" in t:
            return "F4_DRIVER_OR_NET_DECL_ERROR"
        return "F0_IVERILOG_FRONTEND_ERROR"

    if iverilog_ok and not yosys_ok:
        if "include file" in t and "not found" in t:
            return "F0_INCLUDE_NOT_FOUND"
        if "module" in t and ("not found" in t or "unknown" in t):
            return "F1_MODULE_CLOSURE_ERROR"
        if "parameter" in t or "generate" in t:
            return "F2_PARAMETER_GENERATE_ERROR"
        if "multiple conflicting drivers" in t or "multiple drivers" in t:
            return "F4_MULTIPLE_DRIVER_ERROR"
        if "latch" in t:
            return "F5_LATCH_OR_ASSIGNMENT_DISCIPLINE_ERROR"
        if "unsupported" in t or "$display" in t or "$finish" in t:
            return "F6_UNSUPPORTED_OR_NONSYNTHESIZABLE"
        return "F7_YOSYS_SYNTHESIS_FAILURE"

    return "UNKNOWN"
