"""Source-grounded search strata; no inferred semantic mechanism or difficulty."""
from __future__ import annotations

from collections import defaultdict, deque

from r3e.knowledge.verilog_utils import safe_tokenize

DESIGN_CONTEXT_VERSION = "source_structure_strata_v1"


def design_context(carrier):
    values = [t.value for t in safe_tokenize(carrier.clean_rtl)]
    modules = []
    for i, value in enumerate(values[:-1]):
        if value == "module":
            j = i + 1
            if values[j] in {"automatic", "static"}:
                j += 1
            if j < len(values):
                modules.append(values[j])
    parameters = values.count("parameter")
    edges = values.count("posedge") + values.count("negedge")
    return {"version": DESIGN_CONTEXT_VERSION,
            "structural_context": {"module_names": modules, "module_count": len(modules),
                                   "edge_event_count": edges},
            "elaboration_context": {"status": "source_only_not_elaborated",
                                    "parameter_keyword_count": parameters,
                                    "read_only_dependency_count": len(carrier.deps)},
            "stratum": ["multi_module" if len(modules) > 1 else "single_module",
                        "parameterized" if parameters else "no_parameter_declaration",
                        "edge_sensitive" if edges else "no_explicit_edge"],
            "interpretation": "source facts only; not applicability, temporal signature, or repair difficulty"}


def diversify_design_order(carriers):
    """Round-robin source strata, retaining the frozen rotation within each.

    Uses no Blue history/outcomes. The same input produces the same menu in both
    arms; the caller has already restricted this population to discovery.
    """
    groups = defaultdict(deque)
    for carrier in carriers:
        groups[tuple(design_context(carrier)["stratum"])].append(carrier)
    ordered = []
    while groups:
        for key in list(groups):
            ordered.append(groups[key].popleft())
            if not groups[key]:
                del groups[key]
    return ordered
